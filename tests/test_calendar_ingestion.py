"""Fixture-driven tests for ICS parsing and location formatting."""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from pydantic import SecretStr
from requests import RequestException

from dashboard.config import CalendarConfig
from dashboard.__main__ import main as dashboard_main
from dashboard.location import LocationMode, format_location
from dashboard.models import Event
from dashboard.sources import CalendarSourceError, IcsCalendarSource

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ICS_FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "ics"
CHICAGO = ZoneInfo("America/Chicago")
NOW = datetime(2026, 11, 1, 9, 0, tzinfo=CHICAGO)


class MockResponse:
    """Small HTTP response double for deterministic source tests."""

    def __init__(
        self,
        content: bytes,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.content = content
        self.status_code = status_code
        self.headers = headers or {}


class MockSession:
    """Minimal session double recording request details."""

    def __init__(self, *responses: MockResponse | Exception) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def get(self, url: str, **kwargs: object) -> MockResponse:
        self.calls.append({"url": url, **kwargs})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _source(
    *,
    body: bytes = b"",
    url: str = "https://calendar.invalid/private.ics",
    show_declined: bool = True,
    show_tentative: bool = True,
    my_email: str | None = "owner@example.test",
    location_mode: LocationMode = "city",
    session: MockSession | None = None,
) -> tuple[IcsCalendarSource, MockSession]:
    """Create a configured test source with one deterministic HTTP response."""
    calendar_config = CalendarConfig(
        name="Family",
        url_env_var="FAMILY_CALENDAR_URL",
        color="blue",
        enabled=True,
        show_declined=show_declined,
        show_tentative=show_tentative,
        my_email=my_email,
        url=SecretStr(url),
    )
    active_session = session or MockSession(MockResponse(body))
    return (
        IcsCalendarSource(
            calendar_config,
            timezone_name="America/Chicago",
            user_agent="Calendar tests/1.0",
            location_mode=location_mode,
            session=active_session,  # type: ignore[arg-type]
        ),
        active_session,
    )


def _fixture(name: str) -> bytes:
    """Read one checked-in ICS fixture."""
    return (ICS_FIXTURES / name).read_bytes()


def _fetch(source: IcsCalendarSource, *, start: datetime = NOW, days: int = 30) -> list[Event]:
    """Fetch a fixed date window for repeatable tests."""
    return source.fetch_events(start=start, end=start + timedelta(days=days), now=NOW)


@pytest.mark.parametrize(
    ("raw", "mode", "expected"),
    [
        ("123 Main St, Wheaton, IL 60187, USA", "city", "Wheaton"),
        ("123 Main St, Wheaton, IL, USA", "city", "Wheaton"),
        ("123 Main St, Wheaton, IL", "city", "Wheaton"),
        ("123 Main St, Suite 200, Wheaton, IL 60187, USA", "city", "Wheaton"),
        ("500 Oak Ave, Unit 4B, Naperville, IL 60540", "city", "Naperville"),
        ("Wheaton, IL 60187", "city", "Wheaton"),
        ("Wheaton, IL", "city", "Wheaton"),
        ("123 Main St, Chicago, IL 60601-1234", "city", "Chicago"),
        ("The Art Institute of Chicago", "city", "The Art Institute of Chicago"),
        ("Conference Room A", "city", "Conference Room A"),
        ("", "city", ""),
        (None, "city", ""),
        ("https://zoom.us/j/123456", "full", ""),
        ("meet.google.com/abc-defg-hij", "full", ""),
        ("https://teams.microsoft.com/l/meetup-join/abc", "full", ""),
        ("https://example.test/office", "full", "https://example.test/office"),
        ("123 Main St, Wheaton, IL 60187, USA", "full", "123 Main St, Wheaton, IL 60187, USA"),
        ("123 Main St, Wheaton, IL 60187, USA", "none", ""),
    ],
)
def test_format_location(raw: str | None, mode: LocationMode, expected: str) -> None:
    """Format US addresses and suppress conferencing URLs with table-driven cases."""
    assert format_location(raw, mode) == expected


def test_recurring_event_exception_and_exdate_are_expanded() -> None:
    """Expand only occurrences in range while honoring EXDATE and RECURRENCE-ID edits."""
    source, _ = _source(body=_fixture("recurring_exception.ics"))

    events = _fetch(source, days=25)

    assert [(event.title, event.start.date()) for event in events] == [
        ("Weekly team meeting", datetime(2026, 11, 2).date()),
        ("Weekly team meeting moved", datetime(2026, 11, 17).date()),
        ("Weekly team meeting", datetime(2026, 11, 23).date()),
    ]
    assert len({event.uid for event in events}) == len(events)


def test_all_day_and_multiday_events_are_normalized() -> None:
    """Use local aware midnight boundaries and preserve display-ready metadata."""
    all_day_source, _ = _source(body=_fixture("all_day.ics"))
    multi_source, _ = _source(body=_fixture("multi_day.ics"))

    all_day = _fetch(all_day_source)[0]
    multi_day = _fetch(multi_source)[0]

    assert all_day.all_day is True
    assert all_day.start == datetime(2026, 11, 10, tzinfo=CHICAGO)
    assert all_day.end == datetime(2026, 11, 11, tzinfo=CHICAGO)
    assert multi_day.all_day is True
    assert multi_day.end == datetime(2026, 11, 15, tzinfo=CHICAGO)
    assert multi_day.location == "Wheaton"
    assert multi_day.description_short == "Bring warm clothes and hiking shoes."


def test_dst_transition_and_provider_tzids_keep_aware_times() -> None:
    """Resolve DST and Google/iCloud timezone identifiers into aware event times."""
    dst_source, _ = _source(body=_fixture("dst_transition.ics"))
    google_source, _ = _source(body=_fixture("google_tzid.ics"))
    icloud_source, _ = _source(body=_fixture("icloud_tzid.ics"))

    dst = _fetch(
        dst_source,
        start=datetime(2026, 3, 1, tzinfo=CHICAGO),
        days=14,
    )[0]
    google = _fetch(google_source)[0]
    icloud = _fetch(icloud_source)[0]

    assert dst.start.utcoffset() is not None
    assert dst.end.utcoffset() is not None
    assert dst.start.utcoffset().total_seconds() == -6 * 3600
    assert dst.end.utcoffset().total_seconds() == -5 * 3600
    assert google.start.utcoffset().total_seconds() == -6 * 3600
    assert icloud.start.utcoffset().total_seconds() == -8 * 3600


def test_floating_times_use_configured_local_timezone() -> None:
    """Interpret timezone-free date-times in the configured display timezone."""
    source, _ = _source(body=_fixture("floating_time.ics"))

    event = _fetch(source)[0]

    assert event.start == datetime(2026, 11, 11, 9, 0, tzinfo=CHICAGO)
    assert event.end == datetime(2026, 11, 11, 10, 0, tzinfo=CHICAGO)


def test_location_mode_is_applied_to_normalized_events() -> None:
    """Honor full and hidden location modes on event models."""
    full_source, _ = _source(body=_fixture("multi_day.ics"), location_mode="full")
    hidden_source, _ = _source(body=_fixture("multi_day.ics"), location_mode="none")

    assert _fetch(full_source)[0].location == "123 Main St, Suite 200, Wheaton, IL 60187, USA"
    assert _fetch(hidden_source)[0].location is None


def test_status_filtering_uses_event_and_configured_attendee_status() -> None:
    """Drop declined and tentative events only when their respective options are disabled."""
    source, _ = _source(
        body=_fixture("event_statuses.ics"),
        show_declined=False,
        show_tentative=False,
    )

    events = _fetch(source)

    assert events == []


def test_status_values_are_kept_when_enabled_and_cancelled_is_always_dropped() -> None:
    """Retain configured tentative/declined states and exclude cancelled events."""
    source, _ = _source(body=_fixture("event_statuses.ics"))

    events = _fetch(source)

    assert [event.status for event in events] == ["tentative", "tentative", "declined"]


def test_webcal_url_is_converted_and_request_metadata_is_sent() -> None:
    """Convert webcal to HTTPS and send timeout and configured User-Agent."""
    session = MockSession(MockResponse(_fixture("empty.ics")))
    source, _ = _source(url="webcal://calendar.example.test/feed.ics", session=session)

    assert _fetch(source) == []

    assert session.calls[0]["url"] == "https://calendar.example.test/feed.ics"
    assert session.calls[0]["timeout"] == 15.0
    assert session.calls[0]["headers"] == {
        "User-Agent": "Calendar tests/1.0",
        "Accept": "text/calendar, */*",
    }


def test_conditional_headers_and_not_modified_cache() -> None:
    """Reuse the last feed body after a valid 304 and send its validators."""
    session = MockSession(
        MockResponse(_fixture("empty.ics"), headers={"ETag": '"feed-v1"', "Last-Modified": "date"}),
        MockResponse(b"", status_code=304),
    )
    source, _ = _source(session=session)

    assert _fetch(source) == []
    assert _fetch(source) == []

    headers = session.calls[1]["headers"]
    assert isinstance(headers, dict)
    assert headers["If-None-Match"] == '"feed-v1"'
    assert headers["If-Modified-Since"] == "date"


def test_valid_empty_feed_returns_no_events() -> None:
    """An empty but valid VCALENDAR is a successful fetch."""
    source, _ = _source(body=_fixture("empty.ics"))

    assert _fetch(source) == []
    assert source.status.state == "ok"


def test_malformed_feed_raises_clean_typed_error() -> None:
    """Report malformed feed content through the typed, URL-free source error."""
    source, _ = _source(body=_fixture("malformed.ics"))

    with pytest.raises(CalendarSourceError, match="feed is invalid"):
        _fetch(source)

    assert source.status.state == "error"


def test_http_error_is_redacted_from_exception_logs_and_status(caplog: pytest.LogCaptureFixture) -> None:
    """Never include a private feed URL in logged text, errors, or source status."""
    private_url = "https://secret.example.test/secret-calendar-token.ics"
    session = MockSession(MockResponse(b"forbidden", status_code=403))
    source, _ = _source(url=private_url, session=session)

    with caplog.at_level(logging.WARNING), pytest.raises(CalendarSourceError) as error:
        _fetch(source)

    assert private_url not in str(error.value)
    assert private_url not in caplog.text
    assert private_url not in (source.status.message or "")
    assert source.status.state == "error"


def test_request_exception_with_private_url_is_redacted(caplog: pytest.LogCaptureFixture) -> None:
    """Requests exceptions are sanitized even when their own message contains the URL."""
    private_url = "https://secret.example.test/secret-token.ics"
    session = MockSession(RequestException(f"failed to connect to {private_url}"))
    source, _ = _source(url=private_url, session=session)

    with caplog.at_level(logging.WARNING), pytest.raises(CalendarSourceError) as error:
        _fetch(source)

    assert private_url not in str(error.value)
    assert private_url not in caplog.text
    assert private_url not in (source.status.message or "")


def test_failed_refresh_preserves_last_success_status() -> None:
    """A later HTTP failure marks a previously successful source stale."""
    session = MockSession(
        MockResponse(_fixture("empty.ics")),
        MockResponse(b"", status_code=503),
    )
    source, _ = _source(session=session)

    _fetch(source)
    with pytest.raises(CalendarSourceError):
        _fetch(source)

    assert source.status.state == "stale"
    assert source.status.last_success == NOW


def test_fetch_calendars_command_lists_sorted_display_events(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The CLI prints fetched events with time, status, and palette color."""
    calendar = CalendarConfig(
        name="Family",
        url_env_var="FAMILY_CALENDAR_URL",
        color="teal",
        enabled=True,
        url=SecretStr("https://calendar.invalid/private.ics"),
    )
    config = SimpleNamespace(
        calendars=[calendar],
        display=SimpleNamespace(timezone="America/Chicago"),
        weather=SimpleNamespace(user_agent="Calendar tests/1.0"),
        calendar_view=SimpleNamespace(location_mode="city"),
    )
    event = Event(
        calendar_name="Family",
        color="teal",
        title="Soccer",
        start=datetime(2026, 11, 8, 9, 30, tzinfo=CHICAGO),
        end=datetime(2026, 11, 8, 10, 30, tzinfo=CHICAGO),
        all_day=False,
        location="Wheaton",
        description_short=None,
        status="tentative",
    )
    requested: dict[str, datetime] = {}

    class StubSource:
        def __init__(self, _calendar: CalendarConfig, **_kwargs: object) -> None:
            pass

        def fetch_events(
            self, *, start: datetime, end: datetime, now: datetime
        ) -> list[Event]:
            requested.update(start=start, end=end, now=now)
            return [event]

    monkeypatch.setattr("dashboard.__main__.load_config", lambda: config)
    monkeypatch.setattr("dashboard.__main__.IcsCalendarSource", StubSource)
    monkeypatch.setattr(sys, "argv", ["dashboard", "fetch-calendars", "--days", "30"])

    with caplog.at_level(logging.INFO):
        result = dashboard_main()

    assert result == 0
    assert "tentative | teal | Family | Soccer" in caplog.text
    assert requested["end"] - requested["start"] == timedelta(days=30)
