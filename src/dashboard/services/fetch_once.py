"""One-shot source collection and atomic cache-state persistence."""

from __future__ import annotations

import json
import hashlib
import logging
import os
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Collection, Literal
from zoneinfo import ZoneInfo

from dashboard.config import DashboardConfig, PROJECT_ROOT, load_palette
from dashboard.models import (
    CurrentWeather,
    DashboardData,
    DashboardStatus,
    DashboardTheme,
    Event,
    PaletteEntry,
    SourceStatus,
    TodayWeather,
    WeatherSnapshot,
)
from dashboard.services.agenda import merge_events
from dashboard.services.month_layout import layout_calendar
from dashboard.sources import (
    IcsCalendarSource,
    fetch_weather,
)

logger = logging.getLogger(__name__)
DEFAULT_CACHE_PATH = PROJECT_ROOT / "cache" / "state.json"


def fetch_once(
    config: DashboardConfig,
    *,
    now: datetime,
    cache_path: Path | str = DEFAULT_CACHE_PATH,
    calendar_ids: Collection[str] | None = None,
    weather_due: bool = True,
    offline: bool | None = None,
    source_cache_dir: Path | str | None = None,
) -> DashboardData:
    """Fetch selected sources, retain per-source data, and atomically persist dashboard state."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    cache_file = Path(cache_path)
    source_directory = (
        Path(source_cache_dir) if source_cache_dir is not None else cache_file.parent / "sources"
    )
    tz = ZoneInfo(config.display.timezone)
    local_now = now.astimezone(tz)
    previous = _read_previous_state(cache_file)

    events: list[Event] = []
    source_statuses: list[SourceStatus] = []
    day_start = datetime.combine(local_now.date(), time.min, tzinfo=tz)
    fetch_end = day_start + timedelta(days=7 * config.calendar_view.max_weeks)
    for calendar_config in config.calendars:
        if not calendar_config.enabled:
            continue
        selected = calendar_ids is None or calendar_config.url_env_var.lower() in calendar_ids
        source_file = _calendar_cache_path(source_directory, calendar_config.url_env_var)
        cached_events, cached_status = _read_calendar_cache(source_file)
        if not selected:
            events.extend(cached_events)
            source_statuses.append(
                _aged_status(
                    cached_status or _previous_status(previous, calendar_config.url_env_var.lower()),
                    now=local_now,
                    interval=timedelta(minutes=calendar_config.refresh_minutes),
                    kind="calendar",
                    source_id=calendar_config.url_env_var.lower(),
                    name=calendar_config.name,
                )
            )
            continue
        source = IcsCalendarSource(
            calendar_config,
            timezone_name=config.display.timezone,
            user_agent=config.weather.user_agent,
            location_mode=config.calendar_view.location_mode,
        )
        try:
            fetched_events = source.fetch_events(
                start=day_start,
                end=fetch_end,
                now=local_now,
            )
            events.extend(fetched_events)
            source_status = source.status
        except Exception as exc:
            logger.warning(
                "Calendar '%s' fetch failed (%s); retaining its cached data.",
                calendar_config.name,
                type(exc).__name__,
            )
            fetched_events = cached_events
            events.extend(fetched_events)
            source_status = _failed_status(
                cached_status or _previous_status(previous, calendar_config.url_env_var.lower()),
                now=local_now,
                interval=timedelta(minutes=calendar_config.refresh_minutes),
                source_id=calendar_config.url_env_var.lower(),
                name=calendar_config.name,
                kind="calendar",
            )
        _write_calendar_cache(source_file, fetched_events, source_status)
        source_statuses.append(source_status)

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

    previous_weather_status = _previous_status(previous, "weather")
    if not weather_due and previous is not None:
        weather = previous.weather
        weather_status = previous_weather_status or SourceStatus(
            id="weather",
            name="Weather",
            kind="weather",
            state="error",
            last_success=None,
            message="Weather has not been fetched yet.",
        )
    else:
        try:
            weather = fetch_weather(
                config.weather,
                now=local_now,
                timezone_name=config.display.timezone,
            )
            weather_status = SourceStatus(
                id="weather",
                name="Weather",
                kind="weather",
                state="ok",
                last_success=local_now,
                message=None,
            )
        except Exception as exc:
            logger.warning("Weather fetch failed (%s); retaining cached data.", type(exc).__name__)
            if previous is not None:
                weather = previous.weather
                weather_status = _failed_status(
                    previous_weather_status,
                    now=local_now,
                    interval=timedelta(minutes=config.weather.refresh_minutes),
                    source_id="weather",
                    name="Weather",
                    kind="weather",
                    last_success=previous.weather.fetched_at,
                )
            else:
                weather = _empty_weather(config, local_now)
                weather_status = SourceStatus(
                    id="weather",
                    name="Weather",
                    kind="weather",
                    state="error",
                    last_success=None,
                    message="Weather is unavailable; waiting for a successful fetch.",
                )
    source_statuses.append(
        _aged_status(
            weather_status,
            now=local_now,
            interval=timedelta(minutes=config.weather.refresh_minutes),
            kind="weather",
            source_id="weather",
            name="Weather",
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
            offline=offline
            if offline is not None
            else bool(source_statuses) and all(source.state != "ok" for source in source_statuses),
            sources=source_statuses,
        ),
        palette=palette,
        weather=weather,
        calendar=calendar,
    )
    _atomic_write(cache_file, (data.model_dump_json(indent=2) + "\n").encode("utf-8"))
    return data


def empty_dashboard_data(config: DashboardConfig, *, now: datetime) -> DashboardData:
    """Create a valid initial dashboard snapshot without contacting any source."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    local_now = now.astimezone(ZoneInfo(config.display.timezone))
    palette = {
        name: PaletteEntry.model_validate(color.model_dump())
        for name, color in load_palette().items()
    }
    sources = [
        SourceStatus(
            id=calendar.url_env_var.lower(),
            name=calendar.name,
            kind="calendar",
            state="error",
            last_success=None,
            message="Calendar has not been fetched yet.",
        )
        for calendar in config.calendars
        if calendar.enabled
    ]
    sources.append(
        SourceStatus(
            id="weather",
            name="Weather",
            kind="weather",
            state="error",
            last_success=None,
            message="Weather has not been fetched yet.",
        )
    )
    return DashboardData(
        schema_version=1,
        generated_at=local_now,
        timezone=config.display.timezone,
        theme=DashboardTheme(
            night_enabled=config.display.night_theme.enabled,
            night_start=config.display.night_theme.start,
            night_end=config.display.night_theme.end,
        ),
        status=DashboardStatus(offline=False, sources=sources),
        palette=palette,
        weather=_empty_weather(config, local_now),
        calendar=layout_calendar(
            [],
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
        ),
    )


def save_state(data: DashboardData, path: Path | str) -> None:
    """Atomically persist a validated dashboard snapshot."""
    _atomic_write(Path(path), (data.model_dump_json(indent=2) + "\n").encode("utf-8"))


def _read_previous_state(path: Path) -> DashboardData | None:
    """Read valid prior state for source failure isolation without noisy cache contents."""
    try:
        return DashboardData.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except OSError:
        return None
    except (json.JSONDecodeError, ValueError):
        logger.warning("Previous dashboard state is invalid; it will not be used as fallback.")
        return None


def _calendar_cache_path(directory: Path, source_id: str) -> Path:
    """Create a stable filesystem-safe path for one calendar source cache."""
    digest = hashlib.sha256(source_id.lower().encode("utf-8")).hexdigest()
    return directory / f"{digest}.json"


def _read_calendar_cache(path: Path) -> tuple[list[Event], SourceStatus | None]:
    """Load one URL-free per-calendar event and health cache."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        events = [Event.model_validate(item) for item in payload["events"]]
        status = SourceStatus.model_validate(payload["status"])
    except OSError:
        return [], None
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        logger.warning("A calendar source cache is invalid; it will be refreshed.")
        return [], None
    return events, status


def _write_calendar_cache(path: Path, events: list[Event], status: SourceStatus) -> None:
    """Atomically persist one source's normalized events and URL-free health state."""
    payload = {
        "events": [event.model_dump(mode="json") for event in events],
        "status": status.model_dump(mode="json"),
    }
    _atomic_write(path, (json.dumps(payload, indent=2) + "\n").encode("utf-8"))


def _previous_status(data: DashboardData | None, source_id: str) -> SourceStatus | None:
    """Find one source status in a previous contract snapshot."""
    if data is None:
        return None
    return next((source for source in data.status.sources if source.id == source_id), None)


def _aged_status(
    status: SourceStatus | None,
    *,
    now: datetime,
    interval: timedelta,
    kind: Literal["calendar", "weather"],
    source_id: str,
    name: str,
) -> SourceStatus:
    """Mark last-known-good source data stale only after two refresh intervals."""
    if status is None:
        return SourceStatus(
            id=source_id,
            name=name,
            kind=kind,
            state="error",
            last_success=None,
            message="Source has not been fetched yet.",
        )
    if status.last_success is not None and now - status.last_success >= interval * 2:
        return status.model_copy(update={"state": "stale", "message": "Using last-known-good data."})
    return status


def _failed_status(
    previous: SourceStatus | None,
    *,
    now: datetime,
    interval: timedelta,
    source_id: str,
    name: str,
    kind: Literal["calendar", "weather"],
    last_success: datetime | None = None,
) -> SourceStatus:
    """Represent a failed fetch without discarding its cached successful timestamp."""
    successful_at = last_success or (previous.last_success if previous else None)
    stale = successful_at is not None and now - successful_at >= interval * 2
    return SourceStatus(
        id=source_id,
        name=name,
        kind=kind,
        state="stale" if stale else "error",
        last_success=successful_at,
        message="Using last-known-good data." if successful_at is not None else "Source fetch failed.",
    )


def _empty_weather(config: DashboardConfig, now: datetime) -> WeatherSnapshot:
    """Build a contract-valid placeholder while no weather snapshot has succeeded."""
    return WeatherSnapshot(
        units=config.weather.units,
        provider=config.weather.provider,
        fallback_used=False,
        fetched_at=now,
        current=CurrentWeather(
            temp=0,
            feels_like=None,
            condition="Weather unavailable",
            icon_key="unknown",
            precip_chance=None,
        ),
        today=TodayWeather(hi=None, lo=None, precip_chance=None, icon_key="unknown"),
        hourly=[],
        daily=[],
        alerts=[],
        radar_url=None,
    )


def _atomic_write(path: Path, content: bytes) -> None:
    """Persist cache state using a sibling temp file and atomic replacement."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(path.name + ".tmp")
    try:
        with temporary_path.open("wb") as file:
            file.write(content)
            file.flush()
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)
