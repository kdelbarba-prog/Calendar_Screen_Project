"""Fixture-backed, network-free tests for normalized weather providers."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
import requests

from dashboard.config import WeatherConfig
from dashboard.models import DashboardData, WeatherSnapshot
from dashboard.sources import (
    NWSWeatherProvider,
    OpenMeteoWeatherProvider,
    WeatherProviderError,
    fetch_weather,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "weather"
CHICAGO = ZoneInfo("America/Chicago")
NOW = datetime(2026, 11, 8, 10, 0, tzinfo=CHICAGO)


class MockResponse:
    """JSON response double with requests-compatible status handling."""

    def __init__(self, payload: object, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def json(self) -> object:
        return self.payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class RoutedSession:
    """Route HTTP requests to fixture payloads using their non-secret URL paths."""

    def __init__(
        self,
        routes: dict[str, MockResponse | list[MockResponse] | Exception],
    ) -> None:
        self.routes = routes
        self.calls: list[dict[str, object]] = []

    def get(self, url: str, **kwargs: object) -> MockResponse:
        self.calls.append({"url": url, **kwargs})
        route = next((key for key in self.routes if key in url), None)
        if route is None:
            raise AssertionError(f"Unexpected test request path: {url}")
        response_or_list = self.routes[route]
        if isinstance(response_or_list, list):
            response = response_or_list.pop(0)
        else:
            response = response_or_list
        if isinstance(response, Exception):
            raise response
        return response


def _fixture(name: str) -> object:
    """Read one saved JSON response fixture."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _config(**overrides: object) -> WeatherConfig:
    """Create validated weather settings for provider tests."""
    values: dict[str, object] = {
        "provider": "nws",
        "fallback_provider": "openmeteo",
        "lat": 41.8781,
        "lon": -87.6298,
        "units": "imperial",
        "refresh_minutes": 30,
        "user_agent": "Calendar Screen tests/1.0 contact@example.test",
    }
    values.update(overrides)
    return WeatherConfig.model_validate(values)


def _nws_session(observation_name: str = "nws_observation.json") -> RoutedSession:
    """Build a mocked NWS session covering gridpoint, forecast, station, observation, alerts."""
    return RoutedSession(
        {
            "/points/": MockResponse(_fixture("nws_points.json")),
            "/observations/latest": MockResponse(_fixture(observation_name)),
            "/stations": MockResponse(_fixture("nws_stations.json")),
            "/forecast/hourly": MockResponse(_fixture("nws_hourly.json")),
            "/forecast": MockResponse(_fixture("nws_forecast.json")),
            "/alerts/active": MockResponse(_fixture("nws_alerts.json")),
        }
    )


def _openmeteo_session(payload: object | Exception | None = None) -> RoutedSession:
    """Build a mocked Open-Meteo session."""
    response = payload if isinstance(payload, Exception) else MockResponse(
        _fixture("openmeteo.json") if payload is None else payload
    )
    return RoutedSession({"/v1/forecast": response})


def test_nws_provider_resolves_and_caches_station_and_sends_user_agent() -> None:
    """Resolve the nearest station once and normalize the observed current conditions."""
    session = _nws_session()
    provider = NWSWeatherProvider(_config(), timezone_name="America/Chicago", session=session)

    snapshot = provider.fetch_snapshot(now=NOW)

    assert snapshot.provider == "nws"
    assert snapshot.current.temp == pytest.approx(42.98)
    assert snapshot.current.feels_like == pytest.approx(41.0)
    assert snapshot.current.precip_chance == 20
    assert snapshot.today.hi == 51
    assert snapshot.today.lo == 36
    assert snapshot.today.precip_chance == 20
    assert len(snapshot.daily) == 7
    assert snapshot.alerts[0].headline == "Wind Advisory"
    assert provider.status.state == "ok"
    assert all(
        call["headers"]["User-Agent"] == "Calendar Screen tests/1.0 contact@example.test"
        for call in session.calls
    )


@pytest.mark.parametrize(
    "observation_name",
    ["nws_observation_null.json", "nws_observation_stale.json"],
)
def test_nws_uses_hourly_forecast_for_null_or_stale_observation(
    observation_name: str,
) -> None:
    """A missing or older-than-90-minute observation falls back to current hourly forecast."""
    provider = NWSWeatherProvider(
        _config(),
        timezone_name="America/Chicago",
        session=_nws_session(observation_name),
    )

    snapshot = provider.fetch_snapshot(now=NOW)

    assert snapshot.current.temp == 48
    assert snapshot.current.precip_chance == 20


def test_nws_observation_request_failure_falls_back_to_forecast(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed station-observation request does not discard the usable forecast."""
    session = _nws_session()
    session.routes["/observations/latest"] = requests.RequestException("station unavailable")
    provider = NWSWeatherProvider(_config(), timezone_name="America/Chicago", session=session)

    with caplog.at_level(logging.WARNING):
        snapshot = provider.fetch_snapshot(now=NOW)

    assert snapshot.current.temp == 48
    assert snapshot.current.icon_key == "partly_cloudy_day"
    assert "NWS observation unavailable" in caplog.text


def test_nws_station_override_skips_nearest_station_lookup() -> None:
    """An explicit station ID is used directly without querying the station collection."""
    session = _nws_session()
    provider = NWSWeatherProvider(
        _config(nws_station="KORD"),
        timezone_name="America/Chicago",
        session=session,
    )

    provider.fetch_snapshot(now=NOW)

    assert not any("/stations" in str(call["url"]) and "observations" not in str(call["url"])
                   for call in session.calls)
    assert any("/stations/KORD/observations/latest" in str(call["url"]) for call in session.calls)


def test_nws_caches_point_and_nearest_station_for_provider_lifetime() -> None:
    """Repeated snapshots reuse the resolved gridpoint and station identifiers."""
    session = _nws_session()
    provider = NWSWeatherProvider(_config(), timezone_name="America/Chicago", session=session)

    provider.fetch_snapshot(now=NOW)
    provider.fetch_snapshot(now=NOW)

    requested_urls = [str(call["url"]) for call in session.calls]
    assert sum("/points/" in url for url in requested_urls) == 1
    assert sum(url.endswith("/stations") for url in requested_urls) == 1


def test_nws_metric_units_are_requested_and_observation_stays_metric() -> None:
    """Metric configuration requests SI forecast values and honors Celsius observations."""
    session = _nws_session()
    provider = NWSWeatherProvider(
        _config(units="metric"),
        timezone_name="America/Chicago",
        session=session,
    )

    snapshot = provider.fetch_snapshot(now=NOW)

    assert snapshot.current.temp == pytest.approx(6.1)
    assert snapshot.current.feels_like == pytest.approx(5.0)
    assert any("units=si" in str(call["url"]) for call in session.calls)


def test_nws_missing_precipitation_remains_none() -> None:
    """Missing NWS precipitation probabilities are preserved as null values."""
    hourly = _fixture("nws_hourly.json")
    forecast = _fixture("nws_forecast.json")
    assert isinstance(hourly, dict)
    assert isinstance(forecast, dict)
    hourly["properties"]["periods"][0]["probabilityOfPrecipitation"]["value"] = None
    forecast["properties"]["periods"][0]["probabilityOfPrecipitation"]["value"] = None
    session = _nws_session()
    session.routes["/forecast/hourly"] = MockResponse(hourly)
    session.routes["/forecast"] = MockResponse(forecast)
    session.routes["/observations/latest"] = MockResponse(_fixture("nws_observation_null.json"))
    provider = NWSWeatherProvider(_config(), timezone_name="America/Chicago", session=session)

    snapshot = provider.fetch_snapshot(now=NOW)

    assert snapshot.current.precip_chance is None
    assert snapshot.hourly[0].precip_chance is None


def test_openmeteo_maps_current_hourly_and_seven_daily_forecasts() -> None:
    """Normalize Open-Meteo readings into the shared weather snapshot."""
    session = _openmeteo_session()
    provider = OpenMeteoWeatherProvider(
        _config(provider="openmeteo", fallback_provider=None),
        timezone_name="America/Chicago",
        session=session,
    )

    snapshot = provider.fetch_snapshot(now=NOW)

    assert snapshot.provider == "openmeteo"
    assert snapshot.current.temp == pytest.approx(48.2)
    assert snapshot.current.feels_like == pytest.approx(45.1)
    assert snapshot.current.precip_chance == 20
    assert snapshot.current.icon_key == "partly_cloudy_day"
    assert snapshot.today.hi == 51
    assert snapshot.today.lo == 36
    assert snapshot.today.precip_chance == 30
    assert len(snapshot.daily) == 7
    assert snapshot.hourly[-1].precip_chance is None
    assert provider.status.state == "ok"

    params = session.calls[0]["params"]
    assert params["timezone"] == "America/Chicago"
    assert params["forecast_days"] == 7
    assert session.calls[0]["headers"]["User-Agent"] == _config().user_agent


def test_openmeteo_day_night_icon_selection() -> None:
    """Clear-sky conditions select a night variant when the provider marks night."""
    payload = _fixture("openmeteo.json")
    assert isinstance(payload, dict)
    payload["current"]["is_day"] = 0
    payload["current"]["weather_code"] = 0
    session = _openmeteo_session(payload)
    provider = OpenMeteoWeatherProvider(
        _config(provider="openmeteo", fallback_provider=None),
        timezone_name="America/Chicago",
        session=session,
    )

    snapshot = provider.fetch_snapshot(now=NOW)

    assert snapshot.current.icon_key == "clear_night"
    assert snapshot.current.condition == "Clear sky"


def test_openmeteo_missing_precipitation_is_optional() -> None:
    """A provider response with no precipitation arrays still creates valid forecasts."""
    payload = _fixture("openmeteo.json")
    assert isinstance(payload, dict)
    payload["hourly"].pop("precipitation_probability")
    payload["daily"].pop("precipitation_probability_max")
    provider = OpenMeteoWeatherProvider(
        _config(provider="openmeteo", fallback_provider=None),
        timezone_name="America/Chicago",
        session=_openmeteo_session(payload),
    )

    snapshot = provider.fetch_snapshot(now=NOW)

    assert snapshot.current.precip_chance is None
    assert all(point.precip_chance is None for point in snapshot.hourly)
    assert all(point.precip_chance is None for point in snapshot.daily)
    assert snapshot.today.precip_chance is None


def test_snapshot_weather_validates_against_dashboard_data() -> None:
    """Each provider snapshot can be placed into the existing DashboardData contract."""
    snapshot = OpenMeteoWeatherProvider(
        _config(provider="openmeteo", fallback_provider=None),
        timezone_name="America/Chicago",
        session=_openmeteo_session(),
    ).fetch_snapshot(now=NOW)
    sample_path = PROJECT_ROOT / "tests" / "fixtures" / "sample_data.json"
    sample = json.loads(sample_path.read_text(encoding="utf-8"))
    sample["weather"] = snapshot.model_dump(mode="json")

    data = DashboardData.model_validate(sample)

    assert isinstance(data.weather, WeatherSnapshot)
    assert data.weather.provider == "openmeteo"


def test_all_emitted_icon_keys_have_local_svg_assets() -> None:
    """The shared set of provider icon names maps to local-only SVG files."""
    from dashboard.sources.weather_common import ICON_KEYS

    for icon_key in ICON_KEYS:
        assert (PROJECT_ROOT / "static" / "icons" / f"{icon_key}.svg").is_file(), icon_key


def test_primary_failure_uses_fallback_and_marks_fallback_used() -> None:
    """If NWS fails, Open-Meteo supplies the snapshot with the fallback marker set."""
    session = RoutedSession(
        {
            "/points/": MockResponse({}, status_code=503),
            "/v1/forecast": MockResponse(_fixture("openmeteo.json")),
        }
    )

    snapshot = fetch_weather(
        _config(provider="nws", fallback_provider="openmeteo"),
        now=NOW,
        timezone_name="America/Chicago",
        session=session,  # type: ignore[arg-type]
    )

    assert snapshot.provider == "openmeteo"
    assert snapshot.fallback_used is True
    assert [call["url"] for call in session.calls] == [
        "https://api.weather.gov/points/41.8781,-87.6298",
        "https://api.open-meteo.com/v1/forecast",
    ]


def test_two_provider_failure_returns_safe_combined_error() -> None:
    """The fallback error message reports provider names, not endpoint URLs."""
    session = RoutedSession(
        {
            "/points/": MockResponse({}, status_code=503),
            "/v1/forecast": MockResponse({}, status_code=503),
        }
    )

    with pytest.raises(WeatherProviderError, match="both failed") as error:
        fetch_weather(
            _config(),
            now=NOW,
            timezone_name="America/Chicago",
            session=session,  # type: ignore[arg-type]
        )

    assert "api.weather.gov" not in str(error.value)
    assert "api.open-meteo.com" not in str(error.value)


def test_provider_transport_error_does_not_log_request_url(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Provider errors and logs never expose full service endpoint URLs."""
    session = RoutedSession(
        {
            "/points/": requests.RequestException(
                "failed https://api.weather.gov/points/41.8781,-87.6298"
            )
        }
    )
    provider = NWSWeatherProvider(_config(), timezone_name="America/Chicago", session=session)

    with caplog.at_level(logging.WARNING), pytest.raises(WeatherProviderError) as error:
        provider.fetch_snapshot(now=NOW)

    assert "https://api.weather.gov/" not in str(error.value)
    assert "https://api.weather.gov/" not in caplog.text
    assert "https://api.weather.gov/" not in (provider.status.message or "")


def test_weather_cli_prints_snapshot_and_compare(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The CLI emits a JSON snapshot or a two-provider side-by-side summary."""
    config = SimpleNamespace(
        display=SimpleNamespace(timezone="America/Chicago"),
        weather=_config(),
    )
    sample_snapshot = OpenMeteoWeatherProvider(
        _config(provider="openmeteo", fallback_provider=None),
        timezone_name="America/Chicago",
        session=_openmeteo_session(),
    ).fetch_snapshot(now=NOW)

    def fixed_now(tz: ZoneInfo) -> datetime:
        return NOW.astimezone(tz)

    def fake_fetch_weather(
        weather_config: WeatherConfig,
        *,
        now: datetime,
        timezone_name: str,
    ) -> WeatherSnapshot:
        assert weather_config.provider == "nws"
        assert timezone_name == "America/Chicago"
        return sample_snapshot.model_copy(update={"fetched_at": now})

    monkeypatch.setattr("dashboard.__main__.load_config", lambda: config)
    monkeypatch.setattr(
        "dashboard.__main__.datetime",
        SimpleNamespace(now=fixed_now),
    )
    monkeypatch.setattr(
        "dashboard.__main__.fetch_weather",
        fake_fetch_weather,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["dashboard", "fetch-weather"],
    )

    assert __import__("dashboard.__main__", fromlist=["main"]).main() == 0
    output = capsys.readouterr().out
    assert '"provider": "openmeteo"' in output

    class StubProvider:
        def __init__(self, provider_name: str) -> None:
            self.provider_name = provider_name

        def fetch_snapshot(self, *, now: datetime) -> WeatherSnapshot:
            return sample_snapshot.model_copy(
                update={
                    "provider": self.provider_name,
                    "fetched_at": now,
                    "current": sample_snapshot.current.model_copy(
                        update={"temp": 42.0 if self.provider_name == "nws" else 48.2}
                    ),
                }
            )

    def stub_provider(
        name: str,
        weather_config: WeatherConfig,
        *,
        timezone_name: str,
    ) -> StubProvider:
        assert weather_config.provider == "nws"
        assert timezone_name == "America/Chicago"
        return StubProvider(name)

    monkeypatch.setattr("dashboard.__main__.create_weather_provider", stub_provider)
    monkeypatch.setattr(sys, "argv", ["dashboard", "fetch-weather", "--compare"])

    assert __import__("dashboard.__main__", fromlist=["main"]).main() == 0
    compared = capsys.readouterr().out
    assert "nws" in compared
    assert "openmeteo" in compared
    assert "42 F" in compared
