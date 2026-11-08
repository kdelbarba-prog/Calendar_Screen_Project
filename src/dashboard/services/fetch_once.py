"""One-shot source collection and atomic cache-state persistence."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dashboard.config import DashboardConfig, PROJECT_ROOT, load_palette
from dashboard.models import (
    DashboardData,
    DashboardStatus,
    DashboardTheme,
    Event,
    PaletteEntry,
    SourceStatus,
)
from dashboard.renderer import RenderError
from dashboard.services.agenda import merge_events
from dashboard.services.month_layout import layout_calendar
from dashboard.sources import (
    CalendarSourceError,
    IcsCalendarSource,
    WeatherProviderError,
    fetch_weather,
)

logger = logging.getLogger(__name__)
DEFAULT_CACHE_PATH = PROJECT_ROOT / "cache" / "state.json"


def fetch_once(
    config: DashboardConfig,
    *,
    now: datetime,
    cache_path: Path | str = DEFAULT_CACHE_PATH,
) -> DashboardData:
    """Fetch enabled sources once and atomically persist contract-shaped dashboard data."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    cache_file = Path(cache_path)
    tz = ZoneInfo(config.display.timezone)
    local_now = now.astimezone(tz)
    previous = _read_previous_state(cache_file)

    events: list[Event] = []
    source_statuses: list[SourceStatus] = []
    calendar_failed = False
    day_start = datetime.combine(local_now.date(), time.min, tzinfo=tz)
    fetch_end = day_start + timedelta(days=7 * config.calendar_view.max_weeks)
    for calendar_config in config.calendars:
        if not calendar_config.enabled:
            continue
        source = IcsCalendarSource(
            calendar_config,
            timezone_name=config.display.timezone,
            user_agent=config.weather.user_agent,
            location_mode=config.calendar_view.location_mode,
        )
        try:
            events.extend(source.fetch_events(start=day_start, end=fetch_end, now=local_now))
            source_statuses.append(source.status)
        except CalendarSourceError as exc:
            calendar_failed = True
            logger.warning("Calendar '%s' fetch failed: %s", calendar_config.name, exc)
            source_statuses.append(source.status)

    if calendar_failed and previous is not None:
        calendar = previous.calendar
    else:
        calendar = layout_calendar(
            merge_events(events),
            today=local_now.date(),
            timezone_name=config.display.timezone,
            min_weeks=config.calendar_view.min_weeks,
            max_weeks=config.calendar_view.max_weeks,
            line_budget=config.calendar_view.line_budget,
            max_events_per_day=config.calendar_view.max_events_per_day,
            max_bar_lanes=config.calendar_view.max_bar_lanes,
            max_title_chars=config.calendar_view.max_title_chars,
            location_mode=config.calendar_view.location_mode,
            time_format=config.display.time_format,
        )

    try:
        weather = fetch_weather(
            config.weather,
            now=local_now,
            timezone_name=config.display.timezone,
        )
        source_statuses.append(
            SourceStatus(
                id="weather",
                name="Weather",
                kind="weather",
                state="ok",
                last_success=local_now,
                message=None,
            )
        )
    except WeatherProviderError as exc:
        logger.warning("Weather fetch failed: %s", exc)
        if previous is None:
            raise RenderError("Weather is unavailable and no cached dashboard state exists.") from None
        weather = previous.weather
        source_statuses.append(
            SourceStatus(
                id="weather",
                name="Weather",
                kind="weather",
                state="stale",
                last_success=previous.weather.fetched_at,
                message="Using the last successful weather data.",
            )
        )

    palette = {
        name: PaletteEntry.model_validate(color.model_dump())
        for name, color in load_palette().items()
    }
    data = DashboardData(
        schema_version=1,
        generated_at=local_now,
        timezone=config.display.timezone,
        theme=DashboardTheme(
            night_enabled=config.display.night_theme.enabled,
            night_start=config.display.night_theme.start,
            night_end=config.display.night_theme.end,
        ),
        status=DashboardStatus(
            offline=all(source.state != "ok" for source in source_statuses)
            if source_statuses
            else False,
            sources=source_statuses,
        ),
        palette=palette,
        weather=weather,
        calendar=calendar,
    )
    _atomic_write(cache_file, (data.model_dump_json(indent=2) + "\n").encode("utf-8"))
    return data


def _read_previous_state(path: Path) -> DashboardData | None:
    """Read valid prior state for source failure isolation without noisy cache contents."""
    try:
        return DashboardData.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except OSError:
        return None
    except (json.JSONDecodeError, ValueError):
        logger.warning("Previous dashboard state is invalid; it will not be used as fallback.")
        return None


def _atomic_write(path: Path, content: bytes) -> None:
    """Persist cache state using a sibling temp file and atomic replacement."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(path.name + ".tmp")
    try:
        with temporary_path.open("wb") as file:
            file.write(content)
            file.flush()
        os.replace(temporary_path, path)
    except OSError:
        temporary_path.unlink(missing_ok=True)
        raise
