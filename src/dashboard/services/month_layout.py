"""Pure month-grid layout functions for the dashboard calendar contract."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable, Literal
from zoneinfo import ZoneInfo

from dashboard.location import LocationMode, format_location
from dashboard.models import CalendarDay, CalendarView, CalendarWeek, Event, EventBar, EventChip
from dashboard.services.agenda import classify_event

TimeFormat = Literal["12h", "24h"]
_MIDNIGHT = time.min


@dataclass(frozen=True)
class _PreparedEvent:
    """Event data localized and ready for classification and layout."""

    event: Event
    local_start: datetime
    local_end: datetime
    kind: Literal["bar", "chip"]
    event_id: str

    @property
    def first_day(self) -> date:
        """Return the event's local start date."""
        return self.local_start.date()

    @property
    def last_day(self) -> date:
        """Return the last local date touched by the end-exclusive event."""
        if self.local_end.timetz().replace(tzinfo=None) == _MIDNIGHT:
            return self.local_end.date() - timedelta(days=1)
        return self.local_end.date()


def _event_id(event: Event) -> str:
    """Create a stable compact identifier without exposing raw source UIDs."""
    identity = event.uid or "|".join(
        (
            event.calendar_name,
            event.title.casefold(),
            event.start.isoformat(),
            event.end.isoformat(),
        )
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


def _truncate(value: str, max_chars: int) -> str:
    """Truncate display text with an ellipsis while respecting the character limit."""
    normalized = " ".join(value.split())
    if len(normalized) <= max_chars:
        return normalized
    if max_chars <= 3:
        return normalized[:max_chars]
    return normalized[: max_chars - 3].rstrip() + "..."


def _format_time(value: datetime, time_format: TimeFormat) -> str:
    """Format a local event time without platform-specific strftime directives."""
    if time_format == "24h":
        return f"{value.hour:02d}:{value.minute:02d}"
    hour = value.hour % 12 or 12
    suffix = "a" if value.hour < 12 else "p"
    return f"{hour}:{value.minute:02d}{suffix}"


def _time_label(prepared: _PreparedEvent, time_format: TimeFormat) -> str:
    """Format a chip time, including end time for timed overnight events."""
    event = prepared.event
    if event.all_day:
        return ""
    label = _format_time(prepared.local_start, time_format)
    if prepared.local_end.date() > prepared.local_start.date():
        label += f"-{_format_time(prepared.local_end, time_format)}"
    return label


def _prepare_events(
    events: Iterable[Event],
    *,
    timezone_name: str,
    max_title_chars: int,
    location_mode: LocationMode,
) -> list[_PreparedEvent]:
    """Validate, localize, and prepare input events for the layout."""
    tz = ZoneInfo(timezone_name)
    prepared = []
    for event in events:
        if event.start.tzinfo is None or event.start.utcoffset() is None:
            raise ValueError(f"event {event.title!r} start must be timezone-aware")
        if event.end.tzinfo is None or event.end.utcoffset() is None:
            raise ValueError(f"event {event.title!r} end must be timezone-aware")
        local_start = event.start.astimezone(tz)
        local_end = event.end.astimezone(tz)
        if event.end.astimezone(timezone.utc) <= event.start.astimezone(timezone.utc):
            raise ValueError(f"event {event.title!r} must end after it starts")
        display_event = event.model_copy(
            update={
                "title": _truncate(event.title, max_title_chars),
                "location": format_location(event.location, location_mode) or None,
            }
        )
        prepared.append(
            _PreparedEvent(
                event=display_event,
                local_start=local_start,
                local_end=local_end,
                kind=classify_event(event, timezone_name),
                event_id=_event_id(event),
            )
        )
    return sorted(
        prepared,
        key=lambda item: (
            item.event.start.astimezone(timezone.utc),
            item.event.end.astimezone(timezone.utc),
            item.event.title.casefold(),
            item.event.calendar_name.casefold(),
        ),
    )


def _week_start(today: date, first_weekday: int) -> date:
    """Return the first day of the week containing today."""
    if not 0 <= first_weekday <= 6:
        raise ValueError("first_weekday must be between 0 (Monday) and 6 (Sunday)")
    return today - timedelta(days=(today.weekday() - first_weekday) % 7)


def _bar_segment(prepared: _PreparedEvent, week_start: date, lane: int) -> EventBar:
    """Clip an event's bar to one week and compute continuation flags."""
    week_end = week_start + timedelta(days=7)
    clipped_start = max(prepared.first_day, week_start)
    clipped_end = min(prepared.last_day + timedelta(days=1), week_end)
    col_start = (clipped_start - week_start).days
    col_end = (clipped_end - week_start).days
    return EventBar(
        id=prepared.event_id,
        title=prepared.event.title,
        lane=lane,
        col_start=col_start,
        col_span=col_end - col_start,
        continues_left=prepared.first_day < week_start,
        continues_right=prepared.last_day >= week_end,
        calendar=prepared.event.color,
        status=prepared.event.status,
    )


def _layout_week(
    week_start: date,
    today: date,
    events: list[_PreparedEvent],
    *,
    max_events_per_day: int,
    max_bar_lanes: int,
    time_format: TimeFormat,
) -> CalendarWeek:
    """Lay out one week and count all hidden chips and overflow bars."""
    week_end = week_start + timedelta(days=7)
    week_bars = [
        event
        for event in events
        if event.kind == "bar" and event.first_day < week_end and event.last_day >= week_start
    ]
    week_bars.sort(
        key=lambda event: (
            max(event.first_day, week_start),
            -(min(event.last_day + timedelta(days=1), week_end) - max(event.first_day, week_start)).days,
            event.event.title.casefold(),
            event.event_id,
        )
    )

    lane_ends: list[int] = []
    visible_bars: list[EventBar] = []
    overflow_bars: list[_PreparedEvent] = []
    for event in week_bars:
        col_start = (max(event.first_day, week_start) - week_start).days
        col_end = (min(event.last_day + timedelta(days=1), week_end) - week_start).days
        lane = next(
            (index for index, occupied_until in enumerate(lane_ends) if occupied_until <= col_start),
            None,
        )
        if lane is None and len(lane_ends) < max_bar_lanes:
            lane = len(lane_ends)
            lane_ends.append(col_end)
        elif lane is not None:
            lane_ends[lane] = col_end
        if lane is None:
            overflow_bars.append(event)
        else:
            visible_bars.append(_bar_segment(event, week_start, lane))

    days: list[CalendarDay] = []
    for offset in range(7):
        day = week_start + timedelta(days=offset)
        day_chips = [
            event
            for event in events
            if event.kind == "chip" and event.first_day == day
        ]
        day_chips.sort(
            key=lambda event: (
                not event.event.all_day,
                event.event.start.astimezone(timezone.utc),
                event.event.title.casefold(),
                event.event.calendar_name.casefold(),
            )
        )
        shown = day_chips[:max_events_per_day]
        hidden_chip_count = len(day_chips) - len(shown)
        hidden_bar_count = sum(
            event.first_day <= day <= event.last_day for event in overflow_bars
        )
        more_count = hidden_chip_count + hidden_bar_count
        chips = [
            EventChip(
                id=event.event_id,
                title=event.event.title,
                time_label=_time_label(event, time_format),
                location=event.event.location,
                calendar=event.event.color,
                status=event.event.status,
            )
            for event in shown
        ]
        days.append(
            CalendarDay(
                date=day,
                is_today=day == today,
                is_first_of_month=day.day == 1,
                more_count=more_count,
                chips=chips,
            )
        )
    week = CalendarWeek(
        start=week_start,
        lanes=len(lane_ends),
        bars=sorted(visible_bars, key=lambda bar: (bar.lane, bar.col_start, bar.id)),
        days=days,
    )
    return week


def layout_calendar(
    events: Iterable[Event],
    *,
    today: date,
    timezone_name: str,
    min_weeks: int = 2,
    max_weeks: int = 6,
    line_budget: int = 8,
    max_events_per_day: int = 6,
    max_bar_lanes: int = 3,
    max_title_chars: int = 32,
    location_mode: LocationMode = "city",
    time_format: TimeFormat = "12h",
    first_weekday: int = 6,
) -> CalendarView:
    """Build a height-fitted calendar view from events and explicit local today.

    ``line_budget`` remains accepted for API compatibility. The browser fits
    these rendered weeks using their actual content heights, which depend on
    font metrics and the available width.
    """
    if today is None:
        raise ValueError("today must be supplied")
    if not 1 <= min_weeks <= max_weeks <= 6:
        raise ValueError("week limits must satisfy 1 <= min_weeks <= max_weeks <= 6")
    if line_budget < 1:
        raise ValueError("line_budget must be at least 1")
    if max_events_per_day < 1 or max_bar_lanes < 1 or max_title_chars < 1:
        raise ValueError("event, lane, and title limits must be at least 1")

    prepared = _prepare_events(
        events,
        timezone_name=timezone_name,
        max_title_chars=max_title_chars,
        location_mode=location_mode,
    )
    start = _week_start(today, first_weekday)
    weeks: list[CalendarWeek] = []
    for index in range(max_weeks):
        result = _layout_week(
            start + timedelta(days=7 * index),
            today,
            prepared,
            max_events_per_day=max_events_per_day,
            max_bar_lanes=max_bar_lanes,
            time_format=time_format,
        )
        weeks.append(result)

    return CalendarView(today=today, weeks=weeks)


def render_calendar_ascii(calendar: CalendarView) -> str:
    """Render a compact, dependency-free ASCII preview of the calendar grid."""
    weekday_names = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

    def safe_text(value: str) -> str:
        return value.encode("ascii", errors="replace").decode("ascii")

    lines: list[str] = []
    for week in calendar.weeks:
        lines.append(f"Week of {week.start.isoformat()} (lanes: {week.lanes})")
        for bar in week.bars:
            right = bar.col_start + bar.col_span - 1
            left_marker = "<" if bar.continues_left else " "
            right_marker = ">" if bar.continues_right else " "
            lines.append(
                f"  L{bar.lane + 1} {left_marker}{bar.col_start + 1}-{right + 1}"
                f"{right_marker} [{safe_text(bar.calendar)}/{bar.status}] {safe_text(bar.title)}"
            )
        for day in week.days:
            day_label = f"{weekday_names[day.date.weekday()]} {day.date.day:02d}"
            today_marker = "*" if day.is_today else " "
            lines.append(f"  {today_marker}{day_label}")
            for chip in day.chips:
                time_label = f"{chip.time_label} " if chip.time_label else ""
                location = f" ({safe_text(chip.location)})" if chip.location else ""
                lines.append(
                    f"    {time_label}[{safe_text(chip.calendar)}/{chip.status}] "
                    f"{safe_text(chip.title)}{location}"
                )
            if day.more_count:
                lines.append(f"    +{day.more_count} more")
    return "\n".join(lines)
