"""Shared weather normalization helpers."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from dashboard.models import (
    CurrentWeather,
    DailyForecast,
    HourlyForecast,
    TodayWeather,
    WeatherAlert,
)

ICON_KEYS = frozenset(
    {
        "clear_day",
        "clear_night",
        "clear",
        "partly_cloudy_day",
        "partly_cloudy_night",
        "partly_cloudy",
        "cloudy",
        "fog",
        "rain",
        "showers",
        "snow",
        "thunderstorm",
        "windy",
        "unknown",
    }
)

_CONDITION_ICONS = {
    "clear": "clear_day",
    "sunny": "clear_day",
    "mostly sunny": "clear_day",
    "mostly clear": "clear_day",
    "fair": "clear_day",
    "partly cloudy": "partly_cloudy_day",
    "mostly cloudy": "cloudy",
    "cloudy": "cloudy",
    "overcast": "cloudy",
    "fog": "fog",
    "foggy": "fog",
    "mist": "fog",
    "drizzle": "rain",
    "rain": "rain",
    "showers": "rain",
    "snow": "snow",
    "flurries": "snow",
    "sleet": "snow",
    "thunderstorm": "thunderstorm",
    "thunderstorms": "thunderstorm",
    "windy": "windy",
}


def icon_for_condition(condition: str, *, is_day: bool = True) -> str:
    """Map provider wording to a shared icon key, including day/night variants."""
    normalized = condition.casefold().strip()
    icon = next(
        (value for phrase, value in _CONDITION_ICONS.items() if phrase in normalized),
        "unknown",
    )
    if not is_day:
        if icon == "clear_day":
            return "clear_night"
        if icon == "partly_cloudy_day":
            return "partly_cloudy_night"
    return icon


def parse_datetime(value: str, tz: ZoneInfo) -> datetime:
    """Parse provider ISO time and attach local timezone to naive values."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz)


def parse_local_date(value: str) -> date:
    """Parse provider local-date values."""
    return date.fromisoformat(value)


def probability(value: Any) -> int | None:
    """Convert provider precipitation values to a bounded whole percentage."""
    if value is None:
        return None
    number = value.get("value") if isinstance(value, dict) else value
    if number is None:
        return None
    return max(0, min(100, round(float(number))))


def normalize_current(
    *,
    temp: float,
    feels_like: float | None,
    condition: str,
    precip_chance: int | None,
    is_day: bool,
) -> CurrentWeather:
    """Build the shared current-conditions model."""
    return CurrentWeather(
        temp=temp,
        feels_like=feels_like,
        condition=condition,
        icon_key=icon_for_condition(condition, is_day=is_day),
        precip_chance=precip_chance,
    )


def today_summary(
    daily: list[DailyForecast],
    *,
    today: date,
    hourly: list[HourlyForecast],
) -> TodayWeather:
    """Derive today's high/low and precipitation from normalized forecast points."""
    today_forecast = next((item for item in daily if item.date == today), None)
    today_hours = [
        item
        for item in hourly
        if item.time.date() == today
    ]
    chances = [item.precip_chance for item in today_hours if item.precip_chance is not None]
    if today_forecast is not None:
        high, low = today_forecast.hi, today_forecast.lo
        chance = today_forecast.precip_chance
        icon_key = today_forecast.icon_key
    else:
        temperatures = [item.temp for item in today_hours]
        high = max(temperatures) if temperatures else None
        low = min(temperatures) if temperatures else None
        chance = max(chances) if chances else None
        icon_key = today_hours[0].icon_key if today_hours else "unknown"
    if chance is None and chances:
        chance = max(chances)
    return TodayWeather(hi=high, lo=low, precip_chance=chance, icon_key=icon_key)


def day_period_is_day(value: datetime, tz: ZoneInfo) -> bool:
    """Approximate day/night selection when providers do not supply an icon variant."""
    local = value.astimezone(tz)
    return time(6) <= local.timetz().replace(tzinfo=None) < time(18)


def alert_from_nws(feature: dict[str, Any]) -> WeatherAlert | None:
    """Extract a compact NWS alert, ignoring malformed alert entries."""
    properties = feature.get("properties", {})
    headline = properties.get("headline") or properties.get("event")
    severity = properties.get("severity")
    if not headline or not severity:
        return None
    return WeatherAlert(headline=str(headline), severity=str(severity))
