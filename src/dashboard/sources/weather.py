"""Weather provider construction and primary/fallback orchestration."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

import requests

from dashboard.config import WeatherConfig
from dashboard.models import WeatherSnapshot
from dashboard.sources.weather_base import WeatherProvider, WeatherProviderError
from dashboard.sources.weather_nws import NWSWeatherProvider
from dashboard.sources.weather_openmeteo import OpenMeteoWeatherProvider

ProviderName = Literal["nws", "openmeteo"]


def create_weather_provider(
    name: ProviderName,
    config: WeatherConfig,
    *,
    timezone_name: str,
    session: requests.Session | None = None,
) -> WeatherProvider:
    """Create one configured provider instance."""
    if name == "nws":
        return NWSWeatherProvider(
            config,
            timezone_name=timezone_name,
            session=session,
        )
    return OpenMeteoWeatherProvider(
        config,
        timezone_name=timezone_name,
        session=session,
    )


def fetch_weather(
    config: WeatherConfig,
    *,
    now: datetime,
    timezone_name: str,
    session: requests.Session | None = None,
) -> WeatherSnapshot:
    """Fetch from the primary provider, falling back once when it fails."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    primary = create_weather_provider(
        config.provider,
        config,
        timezone_name=timezone_name,
        session=session,
    )
    try:
        return primary.fetch_snapshot(now=now)
    except WeatherProviderError:
        fallback_name = config.fallback_provider
        if fallback_name is None or fallback_name == config.provider:
            raise
        fallback = create_weather_provider(
            fallback_name,
            config,
            timezone_name=timezone_name,
            session=session,
        )
        try:
            snapshot = fallback.fetch_snapshot(now=now)
        except WeatherProviderError as fallback_error:
            raise WeatherProviderError(
                f"Weather providers '{config.provider}' and '{fallback_name}' both failed."
            ) from fallback_error
        return snapshot.model_copy(update={"fallback_used": True})
