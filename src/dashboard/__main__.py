"""Command-line entry point for the dashboard."""

from __future__ import annotations

import argparse
import logging
import sys
import tempfile
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dashboard.config import CONFIG_DIRECTORY, PROJECT_ROOT, ConfigError, load_config
from dashboard.logging_setup import setup_logging
from dashboard.models import DashboardData, Event
from dashboard.renderer import RenderError, apply_scenario, check_budget, render_output
from dashboard.services import layout_calendar, merge_events, render_calendar_ascii
from dashboard.services.fetch_once import DEFAULT_CACHE_PATH, fetch_once
from dashboard.sources import (
    CalendarSourceError,
    IcsCalendarSource,
    WeatherProviderError,
    create_weather_provider,
    fetch_weather,
)

logger = logging.getLogger(__name__)


def _parse_iso_date(value: str) -> date:
    """Parse an ISO local date for the layout-preview command."""
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD format") from exc


def main() -> int:
    """Run the requested dashboard command."""
    parser = argparse.ArgumentParser(description="Calendar screen dashboard")
    commands = parser.add_subparsers(dest="command")
    parser.add_argument("--check-config", action="store_true", help=argparse.SUPPRESS)
    preview_parser = commands.add_parser(
        "layout-preview",
        help="fetch calendars and print an ASCII layout preview",
    )
    preview_parser.add_argument("--today", type=_parse_iso_date, help="override local today (YYYY-MM-DD)")
    fetch_parser = commands.add_parser(
        "fetch-calendars",
        help="fetch and display calendar events",
    )
    fetch_parser.add_argument("--days", type=int, default=30, help="number of days to fetch")
    weather_parser = commands.add_parser(
        "fetch-weather",
        help="fetch and display normalized weather data",
    )
    weather_parser.add_argument(
        "--compare",
        action="store_true",
        help="fetch both providers and compare current temperature and daily summary",
    )
    commands.add_parser(
        "fetch-once",
        help="fetch configured sources and atomically update cache/state.json",
    )
    render_parser = commands.add_parser(
        "render",
        help="render cached dashboard data to output/",
    )
    render_parser.add_argument(
        "--sample",
        action="store_true",
        help="render the committed sample data instead of cache/state.json",
    )
    render_parser.add_argument(
        "--scenario",
        choices=("normal", "busy", "empty", "long-titles", "stale", "offline", "night"),
        default="normal",
        help="apply a sample-only visual test mutation",
    )
    commands.add_parser(
        "check-budget",
        help="check the rendered output and background image size limits",
    )
    args = parser.parse_args()
    setup_logging()

    if args.command == "layout-preview":
        try:
            config = load_config()
        except ConfigError as exc:
            logger.error("Configuration check failed: %s", exc)
            return 1
        timezone = ZoneInfo(config.display.timezone)
        now = datetime.now(timezone)
        today = args.today or now.date()
        today_midnight = datetime.combine(today, time.min, tzinfo=timezone)
        week_start = today_midnight - timedelta(days=(today_midnight.weekday() + 1) % 7)
        fetch_end = week_start + timedelta(days=7 * config.calendar_view.max_weeks)
        events: list[Event] = []
        failed = False
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
                events.extend(source.fetch_events(start=week_start, end=fetch_end, now=now))
            except CalendarSourceError as exc:
                failed = True
                logger.error("%s", exc)

        view = layout_calendar(
            merge_events(events),
            today=today,
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
        sys.stdout.write(render_calendar_ascii(view) + "\n")
        return int(failed)

    if args.command == "fetch-calendars":
        if args.days < 1:
            parser.error("--days must be at least 1")
        try:
            config = load_config()
        except ConfigError as exc:
            logger.error("Configuration check failed: %s", exc)
            return 1
        now = datetime.now(ZoneInfo(config.display.timezone))
        end = now + timedelta(days=args.days)
        events = []
        failed = False
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
                events.extend(source.fetch_events(start=now, end=end, now=now))
            except CalendarSourceError as exc:
                failed = True
                logger.error("%s", exc)

        for event in merge_events(events):
            start_label = event.start.date().isoformat() if event.all_day else event.start.isoformat()
            logger.info(
                "%s | %s | %s | %s | %s",
                start_label,
                event.status,
                event.color,
                event.calendar_name,
                event.title,
            )
        logger.info("Fetched %d event(s) from enabled calendars.", len(events))
        return int(failed)

    if args.command == "fetch-weather":
        try:
            config = load_config()
        except ConfigError as exc:
            logger.error("Configuration check failed: %s", exc)
            return 1
        now = datetime.now(ZoneInfo(config.display.timezone))
        if not args.compare:
            try:
                snapshot = fetch_weather(
                    config.weather,
                    now=now,
                    timezone_name=config.display.timezone,
                )
            except WeatherProviderError as exc:
                logger.error("%s", exc)
                return 1
            sys.stdout.write(snapshot.model_dump_json(indent=2) + "\n")
            return 0

        provider_names = ["nws", "openmeteo"]
        snapshots = {}
        failed = False
        for provider_name in provider_names:
            provider = create_weather_provider(
                provider_name,
                config.weather,
                timezone_name=config.display.timezone,
            )
            try:
                snapshots[provider_name] = provider.fetch_snapshot(now=now)
            except WeatherProviderError as exc:
                failed = True
                logger.error("%s: %s", provider_name, exc)
        lines = [
            "provider       current      hi/lo       precip chance",
            "-------------- ------------ ----------- -------------",
        ]
        for provider_name in provider_names:
            snapshot = snapshots.get(provider_name)
            if snapshot is None:
                lines.append(f"{provider_name:<14} unavailable")
                continue
            temp_unit = "F" if snapshot.units == "imperial" else "C"
            chance = (
                f"{snapshot.today.precip_chance}%"
                if snapshot.today.precip_chance is not None
                else "n/a"
            )
            high = f"{snapshot.today.hi:g}" if snapshot.today.hi is not None else "n/a"
            low = f"{snapshot.today.lo:g}" if snapshot.today.lo is not None else "n/a"
            lines.append(
                f"{provider_name:<14} {snapshot.current.temp:g} {temp_unit:<8} "
                f"{high}/{low:<7} {chance}"
            )
        sys.stdout.write("\n".join(lines) + "\n")
        return int(failed)

    if args.command == "fetch-once":
        try:
            config = load_config()
            now = datetime.now(ZoneInfo(config.display.timezone))
            data = fetch_once(config, now=now)
        except (ConfigError, RenderError, OSError, CalendarSourceError, WeatherProviderError) as exc:
            logger.error("Fetch failed: %s", exc)
            return 1
        logger.info(
            "Fetched dashboard data for %d calendar week(s).",
            len(data.calendar.weeks),
        )
        return 0

    if args.command == "render":
        if not args.sample and args.scenario != "normal":
            parser.error("--scenario requires --sample")
        try:
            config = load_config(
                config_path=CONFIG_DIRECTORY / "config.example.yaml" if args.sample else None
            )
        except ConfigError as exc:
            logger.error("Configuration check failed: %s", exc)
            return 1
        background_path = Path(config.backgrounds.folder)
        if not background_path.is_absolute():
            background_path = PROJECT_ROOT / background_path
        if args.sample:
            sample_path = PROJECT_ROOT / "tests" / "fixtures" / "sample_data.json"
            try:
                data = DashboardData.model_validate_json(sample_path.read_text(encoding="utf-8"))
                if args.scenario != "normal":
                    data = apply_scenario(data, args.scenario)
                with tempfile.TemporaryDirectory(prefix="dashboard-sample-") as temporary:
                    state_path = Path(temporary) / "state.json"
                    state_path.write_text(data.model_dump_json(indent=2), encoding="utf-8")
                    render_output(
                        state_path,
                        PROJECT_ROOT / "output",
                        backgrounds_path=background_path,
                        backgrounds_enabled=config.backgrounds.enabled,
                        change_minutes=config.backgrounds.change_minutes,
                        clock_show_seconds=config.display.clock_show_seconds,
                    )
            except (OSError, ValueError, RenderError) as exc:
                logger.error("Render failed: %s", exc)
                return 1
        else:
            try:
                render_output(
                    DEFAULT_CACHE_PATH,
                    PROJECT_ROOT / "output",
                    backgrounds_path=background_path,
                    backgrounds_enabled=config.backgrounds.enabled,
                    change_minutes=config.backgrounds.change_minutes,
                    clock_show_seconds=config.display.clock_show_seconds,
                )
            except (RenderError, OSError) as exc:
                logger.error("Render failed: %s", exc)
                return 1
        logger.info("Rendered dashboard into output/.")
        return 0

    if args.command == "check-budget":
        passed, issues = check_budget(PROJECT_ROOT / "output")
        if not passed:
            for issue in issues:
                logger.error("%s", issue)
            return 1
        logger.info("Output and backgrounds are within their size budgets.")
        return 0

    if not args.check_config:
        parser.print_help()
        return 0

    try:
        load_config()
    except ConfigError as exc:
        logger.error("Configuration check failed: %s", exc)
        return 1

    logger.info("Configuration is valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
