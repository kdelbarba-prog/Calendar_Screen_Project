"""Shared calendar-source interface and safe source errors."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from dashboard.models import Event, SourceStatus


class CalendarSourceError(RuntimeError):
    """A safe, user-facing calendar error that never includes a feed URL."""


class CalendarSource(ABC):
    """Abstract interface implemented by calendar feed providers."""

    @abstractmethod
    def fetch_events(
        self, *, start: datetime, end: datetime, now: datetime
    ) -> list[Event]:
        """Fetch events overlapping the requested window using the injected clock."""

    @property
    @abstractmethod
    def status(self) -> SourceStatus:
        """Return the latest source status with URL-free messages."""
