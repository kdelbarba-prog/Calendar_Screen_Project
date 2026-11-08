"""Pure-function tests for agenda merging and month-grid layout."""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from dashboard.models import CalendarView, DashboardData, Event, EventStatus
from dashboard.__main__ import main as dashboard_main
from dashboard.services.agenda import classify_event, covered_dates, merge_events
from dashboard.services.month_layout import layout_calendar, render_calendar_ascii

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DATA = PROJECT_ROOT / "tests" / "fixtures" / "sample_data.json"
CHICAGO = ZoneInfo("America/Chicago")


def _event(
    title: str,
    start: datetime,
    end: datetime,
    *,
    uid: str | None = None,
    calendar_name: str = "Family",
    color: str = "blue",
    all_day: bool = False,
    status: EventStatus = "normal",
    location: str | None = None,
) -> Event:
    """Create one validated normalized calendar event."""
    return Event(
        uid=uid,
        calendar_name=calendar_name,
        color=color,
        title=title,
        start=start,
        end=end,
        all_day=all_day,
        location=location,
        description_short=None,
        status=status,
    )


def _at(day: int, hour: int = 9, minute: int = 0, *, month: int = 11) -> datetime:
    """Return a Chicago-local datetime in November 2026."""
    return datetime(2026, month, day, hour, minute, tzinfo=CHICAGO)


def _layout(
    events: list[Event],
    *,
    today: date = date(2026, 11, 1),
    **kwargs: object,
) -> CalendarView:
    """Build a default Sunday-first calendar with test overrides."""
    if "max_weeks" in kwargs:
        kwargs.setdefault("min_weeks", min(2, int(kwargs["max_weeks"])))
    return layout_calendar(
        events,
        today=today,
        timezone_name="America/Chicago",
        **kwargs,  # type: ignore[arg-type]
    )


def test_merge_events_deduplicates_by_uid_and_normalized_title_start() -> None:
    """Merge matching cross-calendar events while retaining distinct UID instances."""
    first = _event("Team meeting", _at(2), _at(2, 10), uid="ical-1", color="blue")
    same_uid = _event(
        "Different imported title",
        _at(2),
        _at(2, 10),
        uid="ical-1",
        calendar_name="Work",
        color="orange",
    )
    same_title_start = _event(
        " team MEETING ",
        _at(2),
        _at(2, 10),
        uid="different-uid",
        calendar_name="Work",
    )
    recurring_next_week = _event(
        "Team meeting",
        _at(9),
        _at(9, 10),
        uid="ical-1#2026-11-09T09:00:00",
    )

    merged = merge_events([first, same_uid, same_title_start, recurring_next_week])

    assert merged == [first, recurring_next_week]
    assert merged[0].color == "blue"


def test_agenda_classifies_multiday_events_and_overnight_chips() -> None:
    """Only multi-day events meeting the duration/all-day rule become bars."""
    multi_day_all_day = _event(
        "Retreat",
        datetime(2026, 11, 2, tzinfo=CHICAGO),
        datetime(2026, 11, 4, tzinfo=CHICAGO),
        all_day=True,
    )
    exactly_24_hours = _event("Long event", _at(2), _at(3))
    short_overnight = _event(
        "Overnight",
        _at(2, 23),
        _at(3, 4),
    )
    daytime = _event("Lunch", _at(2, 12), _at(2, 13))

    assert classify_event(multi_day_all_day, "America/Chicago") == "bar"
    assert classify_event(exactly_24_hours, "America/Chicago") == "bar"
    assert classify_event(short_overnight, "America/Chicago") == "chip"
    assert classify_event(daytime, "America/Chicago") == "chip"
    assert list(covered_dates(short_overnight, "America/Chicago")) == [
        date(2026, 11, 2),
        date(2026, 11, 3),
    ]


def test_bar_crossing_week_boundary_is_clipped_and_marked() -> None:
    """A cross-week bar gets one clipped segment and correct continuation flags per week."""
    event = _event("Conference", _at(6, 8), _at(10, 17))

    calendar = _layout([event], today=date(2026, 11, 1), max_weeks=2, line_budget=30)

    first, second = calendar.weeks
    assert first.bars[0].col_start == 5
    assert first.bars[0].col_span == 2
    assert first.bars[0].continues_left is False
    assert first.bars[0].continues_right is True
    assert second.bars[0].col_start == 0
    assert second.bars[0].col_span == 3
    assert second.bars[0].continues_left is True
    assert second.bars[0].continues_right is False


def test_overlapping_bars_use_distinct_greedy_lanes_and_nonoverlaps_reuse() -> None:
    """Overlapping intervals occupy different lanes; a later interval reuses an open lane."""
    events = [
        _event(
            "A",
            datetime(2026, 11, 2, tzinfo=CHICAGO),
            datetime(2026, 11, 5, tzinfo=CHICAGO),
            uid="a",
            all_day=True,
        ),
        _event(
            "B",
            datetime(2026, 11, 3, tzinfo=CHICAGO),
            datetime(2026, 11, 6, tzinfo=CHICAGO),
            uid="b",
            all_day=True,
        ),
        _event(
            "C",
            datetime(2026, 11, 6, tzinfo=CHICAGO),
            datetime(2026, 11, 8, tzinfo=CHICAGO),
            uid="c",
            all_day=True,
        ),
    ]

    week = _layout(events, max_weeks=1).weeks[0]

    by_title = {bar.title: bar for bar in week.bars}
    assert by_title["A"].lane != by_title["B"].lane
    assert by_title["C"].lane == by_title["A"].lane
    assert week.lanes == 2


def test_long_bar_is_clipped_to_grid_edges() -> None:
    """A bar spanning beyond both visible edges has both continuation markers."""
    event = _event(
        "Long trip",
        datetime(2026, 11, 20, tzinfo=CHICAGO),
        datetime(2027, 1, 10, tzinfo=CHICAGO),
        all_day=True,
    )

    calendar = _layout([event], today=date(2026, 12, 1), min_weeks=2, max_weeks=3, line_budget=30)

    assert len(calendar.weeks) == 3
    assert all(week.bars[0].continues_left for week in calendar.weeks)
    assert all(week.bars[0].continues_right for week in calendar.weeks)
    assert all(week.bars[0].col_start == 0 and week.bars[0].col_span == 7 for week in calendar.weeks)


def test_overflow_bars_and_chips_are_included_in_more_count() -> None:
    """Every omitted chip and lane-overflow bar contributes to each affected day."""
    bars = [
        _event(
            f"Bar {index}",
            datetime(2026, 11, 2, tzinfo=CHICAGO),
            datetime(2026, 11, 6, tzinfo=CHICAGO),
            uid=f"bar-{index}",
            all_day=True,
        )
        for index in range(4)
    ]
    chips = [
        _event(f"Chip {index}", _at(3, 8 + index), _at(3, 8 + index, minute=30))
        for index in range(3)
    ]

    week = _layout(
        bars + chips,
        max_weeks=1,
        max_bar_lanes=2,
        max_events_per_day=1,
    ).weeks[0]

    assert len(week.bars) == 2
    assert week.lanes == 2
    assert week.days[2].more_count == 4
    assert week.days[3].more_count == 2
    assert week.days[4].more_count == 2


def test_dst_weekend_uses_local_dates_and_overnight_end_time() -> None:
    """DST offset changes do not break day classification or local time labels."""
    event = _event(
        "Spring overnight",
        datetime(2026, 3, 8, 0, 30, tzinfo=CHICAGO),
        datetime(2026, 3, 8, 4, 0, tzinfo=CHICAGO),
    )

    calendar = _layout([event], today=date(2026, 3, 8), min_weeks=1, max_weeks=1)

    day = next(day for day in calendar.weeks[0].days if day.date == date(2026, 3, 8))
    assert classify_event(event, "America/Chicago") == "chip"
    assert day.chips[0].time_label == "12:30a"
    assert day.chips[0].title == "Spring overnight"


def test_month_and_year_rollover_produces_complete_sunday_first_weeks() -> None:
    """A year-boundary view stays contiguous and correctly marks the first of January."""
    calendar = _layout([], today=date(2026, 12, 28), min_weeks=2, max_weeks=2)

    first = calendar.weeks[0]
    second = calendar.weeks[1]
    assert first.start == date(2026, 12, 27)
    assert first.days[-1].date == date(2027, 1, 2)
    assert second.start == date(2027, 1, 3)
    assert second.days[0].is_first_of_month is False
    assert second.days[1].date == date(2027, 1, 4)
    assert next(day for day in first.days if day.date == date(2027, 1, 1)).is_first_of_month


def test_all_day_single_chips_sort_before_timed_events_and_have_no_time_label() -> None:
    """Single-day all-day chips lead the list and use the empty time label."""
    timed = _event("Timed", _at(2, 9), _at(2, 10))
    all_day_later_title = _event(
        "Zebra all day",
        datetime(2026, 11, 2, tzinfo=CHICAGO),
        datetime(2026, 11, 3, tzinfo=CHICAGO),
        all_day=True,
    )

    day = _layout([timed, all_day_later_title], max_weeks=1).weeks[0].days[1]

    assert [chip.title for chip in day.chips] == ["Zebra all day", "Timed"]
    assert day.chips[0].time_label == ""


def test_overnight_chip_includes_end_time_and_display_text_is_truncated() -> None:
    """A timed overnight chip shows both times and titles respect the character budget."""
    event = _event(
        "An unusually long overnight event title",
        _at(2, 23),
        _at(3, 1, 15),
        location="123 Main St, Wheaton, IL 60187, USA",
    )

    calendar = _layout([event], max_weeks=1, max_title_chars=15)
    chip = calendar.weeks[0].days[1].chips[0]

    assert chip.time_label == "11:00p-1:15a"
    assert len(chip.title) <= 15
    assert chip.title.endswith("...")
    assert chip.location == "Wheaton"


def test_calendar_auto_fit_respects_min_max_and_monotonic_budget() -> None:
    """The fitter starts at its minimum and uses no more weeks than the budget allows."""
    minimal = _layout([], min_weeks=2, max_weeks=6, line_budget=1)
    medium = _layout([], min_weeks=2, max_weeks=6, line_budget=4)
    generous = _layout([], min_weeks=2, max_weeks=6, line_budget=6)

    assert len(minimal.weeks) == 2
    assert len(medium.weeks) == 4
    assert len(generous.weeks) == 6
    assert 2 <= len(minimal.weeks) <= 6
    assert 2 <= len(medium.weeks) <= 6
    assert 2 <= len(generous.weeks) <= 6


def test_bar_lane_invariant_and_day_event_accounting() -> None:
    """No lane segments overlap, and shown-plus-hidden counts cover every event-day."""
    events = [
        *[
            _event(
                f"Trip {index}",
                datetime(2026, 11, 2 + index, tzinfo=CHICAGO),
                datetime(2026, 11, 6 + index, tzinfo=CHICAGO),
                uid=f"trip-{index}",
                all_day=True,
            )
            for index in range(4)
        ],
        *[
            _event(f"Appointment {index}", _at(4, 8 + index), _at(4, 8 + index, minute=30))
            for index in range(5)
        ],
    ]
    calendar = _layout(
        events,
        max_weeks=2,
        max_bar_lanes=2,
        max_events_per_day=2,
    )
    prepared_types = {event.title: classify_event(event, "America/Chicago") for event in events}

    for week in calendar.weeks:
        for lane in range(week.lanes):
            segments = sorted(
                (bar for bar in week.bars if bar.lane == lane),
                key=lambda bar: bar.col_start,
            )
            assert all(
                left.col_start + left.col_span <= right.col_start
                for left, right in zip(segments, segments[1:])
            )
        for day in week.days:
            all_covering = [
                event
                for event in events
                if (
                    prepared_types[event.title] == "bar"
                    and event.start.astimezone(CHICAGO).date() <= day.date
                    and (
                        event.end.astimezone(timezone.utc) - timedelta(microseconds=1)
                    ).astimezone(CHICAGO).date()
                    >= day.date
                )
                or (
                    prepared_types[event.title] == "chip"
                    and event.start.astimezone(CHICAGO).date() == day.date
                )
            ]
            shown_bars = [
                bar
                for bar in week.bars
                if bar.col_start <= (day.date - week.start).days < bar.col_start + bar.col_span
            ]
            assert len(all_covering) == len(shown_bars) + len(day.chips) + day.more_count


def test_output_validates_against_dashboard_data_contract() -> None:
    """The produced calendar object fits the complete, versioned data contract."""
    calendar = _layout(
        [
            _event(
                "Conference",
                _at(2),
                _at(4),
                all_day=True,
            )
        ],
        min_weeks=1,
        max_weeks=1,
    )
    sample = json.loads(SAMPLE_DATA.read_text(encoding="utf-8"))
    sample["calendar"] = calendar.model_dump(mode="json")

    data = DashboardData.model_validate(sample)

    assert data.calendar == calendar


def test_ascii_preview_contains_week_bars_chips_and_today_marker() -> None:
    """The preview is useful in a terminal and marks today."""
    calendar = _layout(
        [
            _event(
                "Retreat",
                datetime(2026, 11, 2, tzinfo=CHICAGO),
                datetime(2026, 11, 4, tzinfo=CHICAGO),
                all_day=True,
            ),
            _event("Dentist", _at(1, 10), _at(1, 11)),
        ],
        min_weeks=1,
        max_weeks=1,
    )

    preview = render_calendar_ascii(calendar)

    assert "Week of 2026-11-01" in preview
    assert "[blue/normal] Retreat" in preview
    assert "[blue/normal] Dentist" in preview
    assert "*Sun 01" in preview
    assert preview.isascii()


def test_layout_preview_cli_uses_explicit_today_and_emits_ascii(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The CLI accepts an explicit date and prints a calendar without network sources."""
    config = SimpleNamespace(
        display=SimpleNamespace(timezone="America/Chicago", time_format="12h"),
        calendars=[],
        weather=SimpleNamespace(user_agent="Calendar tests/1.0"),
        calendar_view=SimpleNamespace(
            location_mode="city",
            min_weeks=2,
            max_weeks=6,
            line_budget=8,
            max_events_per_day=6,
            max_bar_lanes=3,
            max_title_chars=32,
        ),
    )
    monkeypatch.setattr("dashboard.__main__.load_config", lambda: config)
    monkeypatch.setattr(
        sys,
        "argv",
        ["dashboard", "layout-preview", "--today", "2026-03-08"],
    )

    assert dashboard_main() == 0
    preview = capsys.readouterr().out
    assert "Week of 2026-03-08" in preview
    assert "*Sun 08" in preview


def test_layout_rejects_naive_event_times() -> None:
    """Reject naive datetimes before sorting or date calculations."""
    event = Event.model_construct(
        calendar_name="Family",
        color="blue",
        title="Naive",
        start=datetime(2026, 11, 2, 9),
        end=datetime(2026, 11, 2, 10),
        all_day=False,
        location=None,
        description_short=None,
        status="normal",
    )

    with pytest.raises(ValueError, match="timezone-aware"):
        _layout([event])
