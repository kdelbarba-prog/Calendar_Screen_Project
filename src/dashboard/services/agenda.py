"""Pure event ordering, deduplication, and calendar-day classification."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable, Literal
from zoneinfo import ZoneInfo

from dashboard.models import Event

EventKind = Literal["bar", "chip"]


def _local_datetime(value: datetime, tz: ZoneInfo) -> datetime:
    """Convert an aware event datetime into the configured local timezone."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("calendar events must have timezone-aware start and end values")
    return value.astimezone(tz)


def _utc_datetime(value: datetime) -> datetime:
    """Return an aware datetime in UTC for unambiguous chronological comparisons."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("calendar events must have timezone-aware start and end values")
    return value.astimezone(timezone.utc)


def _last_covered_date(event: Event, tz: ZoneInfo) -> date:
    """Return the last local day touched by an event with an exclusive end."""
    local_end = _local_datetime(event.end, tz)
    if local_end.timetz().replace(tzinfo=None) == time.min:
        return local_end.date() - timedelta(days=1)
    return local_end.date()


def covered_dates(event: Event, timezone_name: str) -> tuple[date, ...]:
    """Return local dates touched by an event, using an end-exclusive boundary."""
    tz = ZoneInfo(timezone_name)
    local_start = _local_datetime(event.start, tz)
    if _utc_datetime(event.end) <= _utc_datetime(event.start):
        raise ValueError(f"event {event.title!r} must end after it starts")
    last_date = _last_covered_date(event, tz)
    return tuple(
        date.fromordinal(day_ordinal)
        for day_ordinal in range(local_start.date().toordinal(), last_date.toordinal() + 1)
    )


def classify_event(event: Event, timezone_name: str) -> EventKind:
    """Classify an event as a multi-day bar or a start-day chip."""
    tz = ZoneInfo(timezone_name)
    local_start = _local_datetime(event.start, tz)
    local_end = _local_datetime(event.end, tz)
    if _utc_datetime(event.end) <= _utc_datetime(event.start):
        raise ValueError(f"event {event.title!r} must end after it starts")

    touched_days = _last_covered_date(event, tz).toordinal() - local_start.date().toordinal() + 1
    elapsed = (local_end.astimezone(timezone.utc) - local_start.astimezone(timezone.utc))
    if touched_days >= 2 and (event.all_day or elapsed >= timedelta(hours=24)):
        return "bar"
    return "chip"


def merge_events(events: Iterable[Event]) -> list[Event]:
    """Deduplicate merged calendar events by UID or normalized title and start."""
    accepted: list[Event] = []
    seen_uids: set[str] = set()
    seen_titles_and_starts: set[tuple[str, datetime]] = set()
    for event in events:
        if event.start.tzinfo is None or event.start.utcoffset() is None:
            raise ValueError(f"event {event.title!r} start must be timezone-aware")
        title_and_start = (event.title.strip().casefold(), event.start.astimezone(timezone.utc))
        if (event.uid is not None and event.uid in seen_uids) or (
            title_and_start in seen_titles_and_starts
        ):
            continue
        accepted.append(event)
        if event.uid is not None:
            seen_uids.add(event.uid)
        seen_titles_and_starts.add(title_and_start)

    return sorted(
        accepted,
        key=lambda event: (
            event.start.astimezone(timezone.utc),
            event.end.astimezone(timezone.utc),
            event.title.casefold(),
            event.calendar_name.casefold(),
        ),
    )
