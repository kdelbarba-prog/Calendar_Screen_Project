"""Shared weather provider interface and safe source errors."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from dashboard.models import SourceStatus, WeatherSnapshot


class WeatherProviderError(RuntimeError):
    """A safe weather-provider error that does not contain request URLs."""


class WeatherProvider(ABC):
    """Abstract interface implemented by weather providers."""

    @abstractmethod
    def fetch_snapshot(self, *, now: datetime) -> WeatherSnapshot:
        """Return a normalized weather snapshot using the injected aware clock."""

    @property
    @abstractmethod
    def status(self) -> SourceStatus:
        """Return the latest URL-free provider health state."""
