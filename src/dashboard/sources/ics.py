"""ICS calendar source with bounded recurrence expansion and safe errors."""

from __future__ import annotations

import logging
import ipaddress
from datetime import date, datetime, time, timedelta
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import recurring_ical_events
import requests
from icalendar import Calendar
from pydantic import SecretStr
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from dashboard.config import CalendarConfig
from dashboard.location import LocationMode, format_location
from dashboard.models import Event, SourceStatus
from dashboard.sources.base import CalendarSource, CalendarSourceError

logger = logging.getLogger(__name__)
DEFAULT_USER_AGENT = "Calendar Screen Project/0.1"
MAX_DESCRIPTION_LENGTH = 240


def _calendar_url(value: str) -> str:
    """Convert an iCalendar webcal URL to HTTPS without otherwise rewriting it."""
    if value.lower().startswith("webcal://"):
        return "https://" + value[len("webcal://") :]
    if value.lower().startswith("webcal:"):
        return "https:" + value[len("webcal:") :]
    return value


def _secret_url(config: CalendarConfig) -> str:
    """Return the configured feed URL without ever including it in errors."""
    configured = config.url
    if isinstance(configured, SecretStr):
        value = configured.get_secret_value()
    elif isinstance(configured, str):
        value = configured
    else:
        value = ""
    if not value:
        raise CalendarSourceError(
            f"Calendar '{config.name}' has no feed URL; configure its environment variable."
        )
    url = _calendar_url(value)
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
    except ValueError:
        hostname = None
        parsed = None
    if parsed is None or parsed.scheme not in {"http", "https"} or not hostname:
        raise CalendarSourceError(
            f"Calendar '{config.name}' has an invalid feed URL; use HTTPS or localhost HTTP."
        )
    if parsed.scheme == "http" and not _is_loopback_host(hostname):
        raise CalendarSourceError(
            f"Calendar '{config.name}' uses HTTP; only localhost feeds are allowed."
        )
    return url


def _is_loopback_host(hostname: str) -> bool:
    """Allow plain HTTP only for loopback addresses and localhost names."""
    host = hostname.rstrip(".").casefold()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _safe_content_description(value: Any) -> str | None:
    """Convert ICS description text to a compact, single-line display string."""
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text[:MAX_DESCRIPTION_LENGTH] or None


def _with_timezone(value: date | datetime, tz: ZoneInfo) -> datetime:
    """Convert an ICS date value to an aware datetime in its source/local timezone."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=tz)
        return value
    return datetime.combine(value, time.min, tzinfo=tz)


def _event_status(component: Any, attendee_email: str | None) -> str:
    """Resolve event status from STATUS and the configured attendee's PARTSTAT."""
    overall_status = str(component.get("STATUS", "")).upper()
    attendee_statuses: list[str] = []
    if attendee_email:
        target = attendee_email.strip().casefold().removeprefix("mailto:")
        attendees = component.get("ATTENDEE", [])
        if not isinstance(attendees, list):
            attendees = [attendees]
        for attendee in attendees:
            address = str(attendee).strip().casefold().removeprefix("mailto:")
            if address == target:
                partstat = attendee.params.get("PARTSTAT", "")
                attendee_statuses.append(str(partstat).upper())

    if "DECLINED" in attendee_statuses:
        return "declined"
    if overall_status == "TENTATIVE" or "TENTATIVE" in attendee_statuses:
        return "tentative"
    return "normal"


def _aware_window(start: datetime, end: datetime, now: datetime) -> tuple[datetime, datetime]:
    """Validate explicit timezone-aware arguments and a non-empty date window."""
    if start.tzinfo is None or start.utcoffset() is None:
        raise ValueError("start must be timezone-aware")
    if end.tzinfo is None or end.utcoffset() is None:
        raise ValueError("end must be timezone-aware")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if end <= start:
        raise ValueError("end must be later than start")
    return start, end


class IcsCalendarSource(CalendarSource):
    """Fetch and normalize one ICS calendar feed."""

    def __init__(
        self,
        config: CalendarConfig,
        *,
        timezone_name: str,
        user_agent: str = DEFAULT_USER_AGENT,
        location_mode: LocationMode = "city",
        timeout: float = 15.0,
        session: requests.Session | None = None,
    ) -> None:
        """Create an ICS source using a calendar configuration and local timezone."""
        self.config = config
        self.timezone = ZoneInfo(timezone_name)
        self.user_agent = user_agent
        self.location_mode = location_mode
        self.timeout = timeout
        self._session = session or requests.Session()
        if session is None:
            retry = Retry(
                total=3,
                connect=3,
                read=3,
                status=3,
                backoff_factor=0.4,
                status_forcelist=(429, 500, 502, 503, 504),
                allowed_methods=frozenset({"GET"}),
                respect_retry_after_header=True,
            )
            adapter = HTTPAdapter(max_retries=retry)
            self._session.mount("http://", adapter)
            self._session.mount("https://", adapter)
        self._cached_body: bytes | None = None
        self._etag: str | None = None
        self._last_modified: str | None = None
        self._last_success: datetime | None = None
        self._status = SourceStatus(
            id=config.url_env_var.lower(),
            name=config.name,
            kind="calendar",
            state="error",
            last_success=None,
            message="Calendar has not been fetched yet.",
        )

    @property
    def status(self) -> SourceStatus:
        """Return the most recent URL-free source health status."""
        return self._status

    def fetch_events(
        self, *, start: datetime, end: datetime, now: datetime
    ) -> list[Event]:
        """Fetch and expand events in the explicit, timezone-aware window."""
        _aware_window(start, end, now)
        try:
            body = self._fetch_body()
            events = self._parse_events(body, start, end)
        except CalendarSourceError as exc:
            self._record_failure(str(exc))
            raise

        self._last_success = now
        self._status = SourceStatus(
            id=self.config.url_env_var.lower(),
            name=self.config.name,
            kind="calendar",
            state="ok",
            last_success=now,
            message=None,
        )
        return sorted(events, key=lambda event: (event.start, event.end, event.title.casefold()))

    def _fetch_body(self) -> bytes:
        """Fetch the feed with conditional headers and never echo request URLs."""
        url = _secret_url(self.config)
        headers = {"User-Agent": self.user_agent, "Accept": "text/calendar, */*"}
        if self._etag:
            headers["If-None-Match"] = self._etag
        if self._last_modified:
            headers["If-Modified-Since"] = self._last_modified
        try:
            response = self._session.get(url, headers=headers, timeout=self.timeout)
        except requests.RequestException as exc:
            logger.warning("Calendar '%s' request failed (%s).", self.config.name, type(exc).__name__)
            raise CalendarSourceError(
                f"Calendar '{self.config.name}' request failed; check connectivity and feed settings."
            ) from None

        if response.status_code == 304:
            if self._cached_body is None:
                raise CalendarSourceError(
                    f"Calendar '{self.config.name}' returned not-modified without cached feed data."
                )
            return self._cached_body
        if not 200 <= response.status_code < 300:
            logger.warning(
                "Calendar '%s' request failed with HTTP status %d.",
                self.config.name,
                response.status_code,
            )
            raise CalendarSourceError(
                f"Calendar '{self.config.name}' request failed with HTTP status "
                f"{response.status_code}."
            )
        self._cached_body = response.content
        self._etag = response.headers.get("ETag")
        self._last_modified = response.headers.get("Last-Modified")
        return self._cached_body

    def _parse_events(self, body: bytes, start: datetime, end: datetime) -> list[Event]:
        """Expand recurring components only for the requested interval and normalize them."""
        try:
            calendar = Calendar.from_ical(body)
            expanded = recurring_ical_events.of(
                calendar,
                skip_bad_series=False,
            ).between(start, end)
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
            logger.warning(
                "Calendar '%s' feed could not be parsed (%s).",
                self.config.name,
                type(exc).__name__,
            )
            raise CalendarSourceError(
                f"Calendar '{self.config.name}' feed is invalid or could not be expanded."
            ) from None

        events: list[Event] = []
        for component in expanded:
            if str(component.get("STATUS", "")).upper() == "CANCELLED":
                continue
            try:
                event = self._normalize_event(component)
            except (TypeError, ValueError, KeyError) as exc:
                logger.warning(
                    "Calendar '%s' contains an unusable event (%s).",
                    self.config.name,
                    type(exc).__name__,
                )
                raise CalendarSourceError(
                    f"Calendar '{self.config.name}' contains an event with invalid date or time data."
                ) from None
            if event.status == "declined" and not self.config.show_declined:
                continue
            if event.status == "tentative" and not self.config.show_tentative:
                continue
            events.append(event)
        return events

    def _normalize_event(self, component: Any) -> Event:
        """Convert one expanded VEVENT into the shared normalized event model."""
        start_property = component.get("DTSTART")
        if start_property is None:
            raise ValueError("VEVENT has no DTSTART")
        start_value = start_property.dt
        all_day = isinstance(start_value, date) and not isinstance(start_value, datetime)
        event_start = _with_timezone(start_value, self.timezone)

        end_property = component.get("DTEND")
        if end_property is not None:
            end_value = end_property.dt
            event_end = _with_timezone(end_value, self.timezone)
        elif component.get("DURATION") is not None:
            event_end = event_start + component.get("DURATION").dt
        else:
            event_end = event_start + (timedelta(days=1) if all_day else timedelta(0))

        if event_end < event_start:
            raise ValueError("VEVENT end precedes start")

        if event_end == event_start:
            event_end = event_start + (timedelta(days=1) if all_day else timedelta(minutes=1))

        status = _event_status(component, self.config.my_email)
        raw_location = component.get("LOCATION")
        location = format_location(
            str(raw_location) if raw_location is not None else None,
            self.location_mode,
        )
        description = _safe_content_description(component.get("DESCRIPTION"))
        title = " ".join(str(component.get("SUMMARY", "(untitled)")).split())
        title = title or "(untitled)"
        uid = component.get("UID")
        recurrence_id = component.get("RECURRENCE-ID")
        if uid is not None and recurrence_id is not None:
            uid = f"{uid}#{recurrence_id.dt.isoformat()}"

        return Event(
            uid=str(uid) if uid is not None else None,
            calendar_name=self.config.name,
            color=self.config.color,
            title=title,
            start=event_start,
            end=event_end,
            all_day=all_day,
            location=location or None,
            description_short=description,
            status=status,
        )

    def _record_failure(self, message: str) -> None:
        """Keep status text generic and retain last success for stale-data callers."""
        stale = self._last_success is not None
        self._status = SourceStatus(
            id=self.config.url_env_var.lower(),
            name=self.config.name,
            kind="calendar",
            state="stale" if stale else "error",
            last_success=self._last_success,
            message=message,
        )
