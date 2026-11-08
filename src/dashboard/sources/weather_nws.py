"""National Weather Service API provider."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from dashboard.config import WeatherConfig
from dashboard.models import (
    CurrentWeather,
    DailyForecast,
    HourlyForecast,
    SourceStatus,
    WeatherAlert,
    WeatherSnapshot,
)
from dashboard.sources.weather_base import WeatherProvider, WeatherProviderError
from dashboard.sources.weather_common import (
    alert_from_nws,
    day_period_is_day,
    icon_for_condition,
    normalize_current,
    parse_datetime,
    probability,
    today_summary,
)

logger = logging.getLogger(__name__)
NWS_API = "https://api.weather.gov"
OBSERVATION_MAX_AGE = timedelta(minutes=90)


def _nws_temperature(value: Any, units: str) -> float | None:
    """Convert an NWS quantity to configured units based on its unitCode."""
    if not isinstance(value, dict) or value.get("value") is None:
        return None
    temp = float(value["value"])
    unit_code = str(value.get("unitCode", "")).casefold()
    if units == "imperial" and "degc" in unit_code:
        return temp * 9 / 5 + 32
    if units == "metric" and ("degf" in unit_code or "fahrenheit" in unit_code):
        return (temp - 32) * 5 / 9
    return temp


def _properties(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a GeoJSON properties object."""
    value = payload.get("properties", {})
    return value if isinstance(value, dict) else {}


class NWSWeatherProvider(WeatherProvider):
    """Fetch normalized current, daily, hourly, and alert data from api.weather.gov."""

    def __init__(
        self,
        config: WeatherConfig,
        *,
        timezone_name: str,
        session: requests.Session | None = None,
        timeout: float = 15.0,
    ) -> None:
        """Create an NWS provider and initialize caches for location resolution."""
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
            "Accept": "application/geo+json, application/json",
        }
        self._point: dict[str, Any] | None = None
        self._station: str | None = config.nws_station
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
        """Return the most recent URL-free provider status."""
        return self._status

    def fetch_snapshot(self, *, now: datetime) -> WeatherSnapshot:
        """Fetch current and forecast data using an injected timezone-aware time."""
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        local_now = now.astimezone(self.timezone)
        try:
            point = self._get_point()
            properties = _properties(point)
            forecast_url = properties.get("forecast")
            hourly_url = properties.get("forecastHourly")
            if not isinstance(forecast_url, str) or not isinstance(hourly_url, str):
                raise WeatherProviderError("NWS location data did not include forecast endpoints.")
            unit_system = "us" if self.config.units == "imperial" else "si"
            forecast_url = self._add_query_parameter(forecast_url, "units", unit_system)
            hourly_url = self._add_query_parameter(hourly_url, "units", unit_system)
            forecast = self._get_json(forecast_url)
            hourly = self._get_json(hourly_url)
            station = self._get_station()

            hourly_periods = _properties(hourly).get("periods", [])
            forecast_periods = _properties(forecast).get("periods", [])
            hourly_points = self._hourly_forecast(hourly_periods, local_now)
            daily_points = self._daily_forecast(forecast_periods, local_now.date())
            try:
                observation = self._get_json(
                    f"{NWS_API}/stations/{station}/observations/latest"
                )
            except WeatherProviderError:
                logger.warning("NWS observation unavailable; using forecast conditions.")
                observation = {}
            current, obs_time = self._current_from_observation(observation, local_now)
            nearest_hour = next(
                (
                    point
                    for point in hourly_points
                    if point.time.astimezone(timezone.utc)
                    >= local_now.astimezone(timezone.utc)
                ),
                hourly_points[0] if hourly_points else None,
            )
            if current is None or obs_time is None or self._observation_is_stale(obs_time, now):
                current = self._current_from_hourly(hourly_points, local_now)
            elif nearest_hour is not None:
                current = current.model_copy(
                    update={"precip_chance": nearest_hour.precip_chance}
                )
            if current is None:
                raise WeatherProviderError("NWS returned no usable current or forecast temperature.")
            alerts = self._alerts()
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
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            logger.warning("NWS response could not be normalized (%s).", type(exc).__name__)
            error = WeatherProviderError("NWS returned incomplete or invalid weather data.")
            self._status = SourceStatus(
                id="weather",
                name="Weather",
                kind="weather",
                state="error" if self._status.last_success is None else "stale",
                last_success=self._status.last_success,
                message=str(error),
            )
            raise error from None

        snapshot = WeatherSnapshot(
            units=self.config.units,
            provider="nws",
            fallback_used=False,
            fetched_at=now,
            current=current,
            today=today_summary(daily_points, today=local_now.date(), hourly=hourly_points),
            hourly=hourly_points,
            daily=daily_points,
            alerts=alerts,
            radar_url=None,
        )
        self._status = SourceStatus(
            id="weather",
            name="Weather",
            kind="weather",
            state="ok",
            last_success=now,
            message=None,
        )
        return snapshot

    @staticmethod
    def _add_query_parameter(url: str, key: str, value: str) -> str:
        """Append a query parameter to a public NWS endpoint."""
        separator = "&" if "?" in url else "?"
        return f"{url}{separator}{key}={value}"

    def _get_json(self, url: str) -> dict[str, Any]:
        """Retrieve a JSON document and raise a URL-free provider error."""
        try:
            response = self._session.get(url, headers=self._headers, timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            logger.warning("NWS request failed (%s).", type(exc).__name__)
            raise WeatherProviderError(
                "NWS request failed; check connectivity and provider configuration."
            ) from None
        except ValueError:
            logger.warning("NWS returned invalid JSON.")
            raise WeatherProviderError("NWS returned an invalid response.") from None
        if not isinstance(payload, dict):
            raise WeatherProviderError("NWS returned an unexpected response shape.")
        return payload

    def _get_point(self) -> dict[str, Any]:
        """Resolve and cache the NWS gridpoint for configured coordinates."""
        if self._point is None:
            self._point = self._get_json(f"{NWS_API}/points/{self.config.lat},{self.config.lon}")
        return self._point

    def _get_station(self) -> str:
        """Use a station override or resolve and cache the nearest NWS station."""
        if self._station:
            return self._station
        station_url = _properties(self._get_point()).get("observationStations")
        if not isinstance(station_url, str):
            raise WeatherProviderError("NWS location data did not include observation stations.")
        station_data = self._get_json(station_url)
        features = station_data.get("features", [])
        if not features or not isinstance(features[0], dict):
            raise WeatherProviderError("NWS returned no nearby observation stations.")
        station_id = features[0].get("properties", {}).get("stationIdentifier")
        if not station_id:
            station_id = str(features[0].get("id", "")).rstrip("/").split("/")[-1]
        if not station_id:
            raise WeatherProviderError("NWS returned an invalid observation station.")
        self._station = str(station_id)
        return self._station

    def _current_from_observation(
        self, payload: dict[str, Any], now: datetime
    ) -> tuple[CurrentWeather | None, datetime | None]:
        """Normalize an observation payload if it includes a temperature."""
        properties = _properties(payload)
        raw_temp = properties.get("temperature")
        temp = _nws_temperature(raw_temp, self.config.units)
        timestamp = properties.get("timestamp")
        if temp is None or not isinstance(timestamp, str):
            return None, None
        observed_at = parse_datetime(timestamp, self.timezone)
        condition = str(properties.get("textDescription") or "Unknown")
        is_day = day_period_is_day(now, self.timezone)
        feels_like = _nws_temperature(
            properties.get("heatIndex") or properties.get("windChill"),
            self.config.units,
        )
        return (
            self._current(
                temp=temp,
                feels_like=feels_like,
                condition=condition,
                precip_chance=None,
                is_day=is_day,
            ),
            observed_at,
        )

    @staticmethod
    def _observation_is_stale(observed_at: datetime, now: datetime) -> bool:
        """Treat observations older than 90 minutes or more than 5 minutes ahead as stale."""
        age = now.astimezone(timezone.utc) - observed_at.astimezone(timezone.utc)
        return age > OBSERVATION_MAX_AGE or age < -timedelta(minutes=5)

    def _hourly_forecast(
        self, periods: Any, now: datetime
    ) -> list[HourlyForecast]:
        """Normalize up to the next 24 NWS hourly forecast periods."""
        if not isinstance(periods, list):
            return []
        results = []
        for item in periods:
            if not isinstance(item, dict):
                continue
            start_value = item.get("startTime")
            temperature = item.get("temperature")
            if not isinstance(start_value, str) or temperature is None:
                continue
            start = parse_datetime(start_value, self.timezone)
            if start.astimezone(timezone.utc) < now.astimezone(timezone.utc):
                continue
            condition = str(item.get("shortForecast") or "Unknown")
            is_day = item.get("isDaytime", day_period_is_day(start, self.timezone))
            results.append(
                HourlyForecast(
                    time=start,
                    temp=float(temperature),
                    precip_chance=probability(item.get("probabilityOfPrecipitation")),
                    icon_key=icon_for_condition(condition, is_day=bool(is_day)),
                )
            )
            if len(results) >= 24:
                break
        return results

    def _daily_forecast(self, periods: Any, today: date) -> list[DailyForecast]:
        """Group NWS day/night periods into seven local-date summaries."""
        grouped: dict[date, list[dict[str, Any]]] = {}
        if isinstance(periods, list):
            for item in periods:
                if not isinstance(item, dict) or not isinstance(item.get("startTime"), str):
                    continue
                local_date = parse_datetime(item["startTime"], self.timezone).date()
                if today <= local_date < today + timedelta(days=7):
                    grouped.setdefault(local_date, []).append(item)

        results: list[DailyForecast] = []
        for offset in range(7):
            forecast_date = today + timedelta(days=offset)
            entries = grouped.get(forecast_date, [])
            daytime = next((entry for entry in entries if entry.get("isDaytime")), None)
            nighttime = next((entry for entry in reversed(entries) if not entry.get("isDaytime")), None)
            primary = daytime or (entries[0] if entries else {})
            high = _nws_temperature(
                {
                    "value": primary.get("temperature"),
                    "unitCode": (
                        "wmoUnit:degF"
                        if self.config.units == "imperial"
                        else "wmoUnit:degC"
                    ),
                },
                self.config.units,
            ) if primary.get("temperature") is not None else None
            low_entry = nighttime or primary
            low = _nws_temperature(
                {
                    "value": low_entry.get("temperature"),
                    "unitCode": (
                        "wmoUnit:degF"
                        if self.config.units == "imperial"
                        else "wmoUnit:degC"
                    ),
                },
                self.config.units,
            ) if low_entry.get("temperature") is not None else None
            precipitation = [
                probability(entry.get("probabilityOfPrecipitation"))
                for entry in entries
            ]
            chances = [chance for chance in precipitation if chance is not None]
            condition = str(primary.get("shortForecast") or "Unknown")
            is_day = bool(primary.get("isDaytime", True))
            results.append(
                DailyForecast(
                    date=forecast_date,
                    hi=high,
                    lo=low,
                    precip_chance=max(chances) if chances else None,
                    icon_key=icon_for_condition(condition, is_day=is_day),
                )
            )
        return results

    def _current_from_hourly(
        self, hourly: list[HourlyForecast], now: datetime
    ) -> CurrentWeather | None:
        """Use the nearest forecast period for current conditions when observation is unusable."""
        if not hourly:
            return None
        forecast = next(
            (
                item
                for item in hourly
                if item.time.astimezone(timezone.utc) >= now.astimezone(timezone.utc)
            ),
            hourly[0],
        )
        is_day = forecast.icon_key not in {"clear_night", "partly_cloudy_night"}
        condition = forecast.icon_key.replace("_", " ").title()
        return self._current(
            temp=forecast.temp,
            feels_like=None,
            condition=condition,
            precip_chance=forecast.precip_chance,
            is_day=is_day,
        )

    @staticmethod
    def _current(
        *,
        temp: float,
        feels_like: float | None,
        condition: str,
        precip_chance: int | None,
        is_day: bool,
    ) -> CurrentWeather:
        """Build a shared current-weather value."""
        return normalize_current(
            temp=temp,
            feels_like=feels_like,
            condition=condition,
            precip_chance=precip_chance,
            is_day=is_day,
        )

    def _alerts(self) -> list[WeatherAlert]:
        """Fetch active alerts for the configured point; omit them if optional service fails."""
        try:
            payload = self._get_json(
                f"{NWS_API}/alerts/active?point={self.config.lat},{self.config.lon}"
            )
        except WeatherProviderError:
            logger.warning("NWS active alerts are unavailable; continuing without alerts.")
            return []
        features = payload.get("features", [])
        if not isinstance(features, list):
            return []
        alerts = [alert_from_nws(item) for item in features if isinstance(item, dict)]
        return [alert for alert in alerts if alert is not None]
