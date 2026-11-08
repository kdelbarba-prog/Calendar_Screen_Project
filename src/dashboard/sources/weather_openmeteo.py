"""Open-Meteo weather API provider."""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from dashboard.config import WeatherConfig
from dashboard.models import (
    DailyForecast,
    HourlyForecast,
    SourceStatus,
    WeatherSnapshot,
)
from dashboard.sources.weather_base import WeatherProvider, WeatherProviderError
from dashboard.sources.weather_common import (
    icon_for_condition,
    normalize_current,
    parse_datetime,
    today_summary,
)

logger = logging.getLogger(__name__)
OPEN_METEO_API = "https://api.open-meteo.com/v1/forecast"

_WMO_CONDITIONS = {
    0: ("Clear sky", "clear"),
    1: ("Mainly clear", "clear"),
    2: ("Partly cloudy", "partly_cloudy"),
    3: ("Overcast", "cloudy"),
    45: ("Fog", "fog"),
    48: ("Depositing rime fog", "fog"),
    51: ("Light drizzle", "rain"),
    53: ("Drizzle", "rain"),
    55: ("Dense drizzle", "rain"),
    56: ("Freezing drizzle", "rain"),
    57: ("Freezing drizzle", "rain"),
    61: ("Light rain", "rain"),
    63: ("Rain", "rain"),
    65: ("Heavy rain", "rain"),
    66: ("Freezing rain", "rain"),
    67: ("Heavy freezing rain", "rain"),
    71: ("Light snow", "snow"),
    73: ("Snow", "snow"),
    75: ("Heavy snow", "snow"),
    77: ("Snow grains", "snow"),
    80: ("Rain showers", "rain"),
    81: ("Rain showers", "rain"),
    82: ("Heavy rain showers", "rain"),
    85: ("Snow showers", "snow"),
    86: ("Heavy snow showers", "snow"),
    95: ("Thunderstorm", "thunderstorm"),
    96: ("Thunderstorm with hail", "thunderstorm"),
    99: ("Thunderstorm with hail", "thunderstorm"),
}


def _nullable_number(value: Any) -> float | None:
    """Convert numeric JSON values while preserving missing values."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _condition(code: Any) -> tuple[str, str]:
    """Map WMO weather code to a readable condition and shared icon family."""
    try:
        return _WMO_CONDITIONS.get(int(code), ("Unknown", "unknown"))
    except (TypeError, ValueError):
        return "Unknown", "unknown"


class OpenMeteoWeatherProvider(WeatherProvider):
    """Fetch and normalize Open-Meteo current and seven-day forecast data."""

    def __init__(
        self,
        config: WeatherConfig,
        *,
        timezone_name: str,
        session: requests.Session | None = None,
        timeout: float = 15.0,
    ) -> None:
        """Create an Open-Meteo provider."""
        self.config = config
        self.timezone = ZoneInfo(timezone_name)
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
            )
            adapter = HTTPAdapter(max_retries=retry)
            self._session.mount("https://", adapter)
        self._headers = {
            "User-Agent": config.user_agent,
            "Accept": "application/json",
        }
        self._status = SourceStatus(
            id="weather",
            name="Weather",
            kind="weather",
            state="error",
            last_success=None,
            message="Weather has not been fetched yet.",
        )

    @property
    def status(self) -> SourceStatus:
        """Return the latest URL-free provider health status."""
        return self._status

    def fetch_snapshot(self, *, now: datetime) -> WeatherSnapshot:
        """Fetch current, hourly, and daily forecast data using an injected clock."""
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        try:
            payload = self._fetch_json()
            try:
                snapshot = self._normalize(payload, now)
            except (KeyError, TypeError, ValueError, OverflowError) as exc:
                logger.warning("Open-Meteo response could not be normalized (%s).", type(exc).__name__)
                raise WeatherProviderError(
                    "Open-Meteo returned incomplete or invalid weather data."
                ) from None
        except WeatherProviderError as exc:
            self._status = SourceStatus(
                id="weather",
                name="Weather",
                kind="weather",
                state="error" if self._status.last_success is None else "stale",
                last_success=self._status.last_success,
                message=str(exc),
            )
            raise
        self._status = SourceStatus(
            id="weather",
            name="Weather",
            kind="weather",
            state="ok",
            last_success=now,
            message=None,
        )
        return snapshot

    def _fetch_json(self) -> dict[str, Any]:
        """Request Open-Meteo and convert transport/response errors to safe messages."""
        temp_unit = "fahrenheit" if self.config.units == "imperial" else "celsius"
        params = {
            "latitude": self.config.lat,
            "longitude": self.config.lon,
            "timezone": str(self.timezone),
            "forecast_days": 7,
            "temperature_unit": temp_unit,
            "current": (
                "temperature_2m,relative_humidity_2m,apparent_temperature,"
                "is_day,precipitation,weather_code"
            ),
            "hourly": "temperature_2m,precipitation_probability,weather_code,is_day",
            "daily": (
                "temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
                "weather_code"
            ),
        }
        try:
            response = self._session.get(
                OPEN_METEO_API,
                params=params,
                headers=self._headers,
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            logger.warning("Open-Meteo request failed (%s).", type(exc).__name__)
            raise WeatherProviderError(
                "Open-Meteo request failed; check connectivity and provider configuration."
            ) from None
        except ValueError:
            logger.warning("Open-Meteo returned invalid JSON.")
            raise WeatherProviderError("Open-Meteo returned an invalid response.") from None
        if not isinstance(payload, dict) or not all(
            isinstance(payload.get(section), dict) for section in ("current", "hourly", "daily")
        ):
            raise WeatherProviderError("Open-Meteo returned an unexpected response shape.")
        return payload

    def _normalize(self, payload: dict[str, Any], now: datetime) -> WeatherSnapshot:
        """Map Open-Meteo JSON sections into the shared snapshot model."""
        current_data = payload["current"]
        current_time = parse_datetime(
            str(current_data.get("time", now.isoformat())),
            self.timezone,
        )
        condition, _ = _condition(current_data.get("weather_code"))
        is_day = bool(current_data.get("is_day", 1))
        current = normalize_current(
            temp=float(current_data["temperature_2m"]),
            feels_like=_nullable_number(current_data.get("apparent_temperature")),
            condition=condition,
            precip_chance=None,
            is_day=is_day,
        )

        hourly_data = payload["hourly"]
        hourly_times = hourly_data.get("time", [])
        hourly_temps = hourly_data.get("temperature_2m", [])
        hourly_chances = hourly_data.get("precipitation_probability", [])
        hourly_codes = hourly_data.get("weather_code", [])
        hourly_days = hourly_data.get("is_day", [])
        hourly: list[HourlyForecast] = []
        if all(isinstance(value, list) for value in (hourly_times, hourly_temps)):
            for index, timestamp in enumerate(hourly_times):
                if index >= len(hourly_temps) or hourly_temps[index] is None:
                    continue
                forecast_time = parse_datetime(str(timestamp), self.timezone)
                if forecast_time.astimezone(timezone.utc) < now.astimezone(timezone.utc):
                    continue
                chance = (
                    int(hourly_chances[index])
                    if index < len(hourly_chances) and hourly_chances[index] is not None
                    else None
                )
                code = hourly_codes[index] if index < len(hourly_codes) else None
                daytime = bool(hourly_days[index]) if index < len(hourly_days) else 6 <= forecast_time.hour < 18
                condition, _ = _condition(code)
                hourly.append(
                    HourlyForecast(
                        time=forecast_time,
                        temp=float(hourly_temps[index]),
                        precip_chance=chance,
                        icon_key=icon_for_condition(condition, is_day=daytime),
                    )
                )
                if len(hourly) >= 24:
                    break

        daily_data = payload["daily"]
        daily_dates = daily_data.get("time", [])
        highs = daily_data.get("temperature_2m_max", [])
        lows = daily_data.get("temperature_2m_min", [])
        chances = daily_data.get("precipitation_probability_max", [])
        codes = daily_data.get("weather_code", [])
        if not isinstance(daily_dates, list) or len(daily_dates) < 7:
            raise WeatherProviderError("Open-Meteo did not return a seven-day forecast.")
        daily: list[DailyForecast] = []
        for index, date_text in enumerate(daily_dates[:7]):
            high = _nullable_number(highs[index]) if index < len(highs) else None
            low = _nullable_number(lows[index]) if index < len(lows) else None
            chance = (
                int(chances[index])
                if index < len(chances) and chances[index] is not None
                else None
            )
            code = codes[index] if index < len(codes) else None
            condition, _ = _condition(code)
            daily.append(
                DailyForecast(
                    date=date.fromisoformat(str(date_text)),
                    hi=high,
                    lo=low,
                    precip_chance=chance,
                    icon_key=icon_for_condition(condition, is_day=True),
                )
            )

        matching_hour = next(
            (
                point
                for point in hourly
                if point.time.astimezone(timezone.utc)
                == current_time.astimezone(timezone.utc)
            ),
            None,
        )
        current = current.model_copy(
            update={
                "precip_chance": matching_hour.precip_chance if matching_hour else None
            }
        )
        return WeatherSnapshot(
            units=self.config.units,
            provider="openmeteo",
            fallback_used=False,
            fetched_at=now,
            current=current,
            today=today_summary(daily, today=now.astimezone(self.timezone).date(), hourly=hourly),
            hourly=hourly,
            daily=daily,
            alerts=[],
            radar_url=None,
        )
