"""Calendar and weather data sources."""

from dashboard.sources.base import CalendarSource, CalendarSourceError
from dashboard.sources.ics import IcsCalendarSource
from dashboard.sources.weather import create_weather_provider, fetch_weather
from dashboard.sources.weather_base import WeatherProvider, WeatherProviderError
from dashboard.sources.weather_nws import NWSWeatherProvider
from dashboard.sources.weather_openmeteo import OpenMeteoWeatherProvider

__all__ = [
    "CalendarSource",
    "CalendarSourceError",
    "IcsCalendarSource",
    "NWSWeatherProvider",
    "OpenMeteoWeatherProvider",
    "WeatherProvider",
    "WeatherProviderError",
    "create_weather_provider",
    "fetch_weather",
]
