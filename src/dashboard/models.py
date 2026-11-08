"""Typed data models for source events and the rendered dashboard contract."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

EventStatus = Literal["normal", "tentative", "declined"]
SourceState = Literal["ok", "stale", "error"]


class StrictModel(BaseModel):
    """Base model for strict serialized dashboard data."""

    model_config = ConfigDict(extra="forbid")


class Event(StrictModel):
    """A normalized calendar event before layout."""

    uid: str | None = None
    calendar_name: str
    color: str
    title: str
    start: AwareDatetime
    end: AwareDatetime
    all_day: bool
    location: str | None
    description_short: str | None
    status: EventStatus


class SourceStatus(StrictModel):
    """Health and latest-success information for one data source."""

    id: str
    name: str
    kind: Literal["calendar", "weather"]
    state: SourceState
    last_success: AwareDatetime | None
    message: str | None


class DashboardStatus(StrictModel):
    """Global and per-source health state."""

    offline: bool
    sources: list[SourceStatus]


class DashboardTheme(StrictModel):
    """Night theme settings serialized for the display."""

    night_enabled: bool
    night_start: str
    night_end: str


class PaletteEntry(StrictModel):
    """Display colors for one palette key."""

    bg: str
    fg: str
    tentative_bg: str
    tentative_fg: str


class CurrentWeather(StrictModel):
    """Current observation fields in the data contract."""

    temp: float
    feels_like: float | None
    condition: str
    icon_key: str
    precip_chance: int | None


class TodayWeather(StrictModel):
    """Today's summary in the data contract."""

    hi: float | None
    lo: float | None
    precip_chance: int | None
    icon_key: str


class HourlyForecast(StrictModel):
    """One hourly forecast point."""

    time: AwareDatetime
    temp: float
    precip_chance: int | None
    icon_key: str


class DailyForecast(StrictModel):
    """One local-date forecast summary."""

    date: date
    hi: float | None
    lo: float | None
    precip_chance: int | None
    icon_key: str


class WeatherAlert(StrictModel):
    """A compact weather alert for display."""

    headline: str
    severity: str


class WeatherSnapshot(StrictModel):
    """Weather payload shared by providers and the dashboard contract."""

    units: Literal["imperial", "metric"]
    provider: Literal["nws", "openmeteo"]
    fallback_used: bool
    fetched_at: AwareDatetime
    current: CurrentWeather
    today: TodayWeather
    hourly: list[HourlyForecast]
    daily: list[DailyForecast]
    alerts: list[WeatherAlert]
    radar_url: str | None


class EventBar(StrictModel):
    """A multi-day event segment within one calendar week."""

    id: str
    title: str
    lane: int = Field(ge=0)
    col_start: int = Field(ge=0, le=6)
    col_span: int = Field(ge=1, le=7)
    continues_left: bool
    continues_right: bool
    calendar: str
    status: EventStatus


class EventChip(StrictModel):
    """A single-day event displayed inside its start-day cell."""

    id: str
    title: str
    time_label: str
    location: str | None
    calendar: str
    status: EventStatus


class CalendarDay(StrictModel):
    """Calendar cell data for one local day."""

    date: date
    is_today: bool
    is_first_of_month: bool
    more_count: int = Field(ge=0)
    chips: list[EventChip]


class CalendarWeek(StrictModel):
    """One rendered calendar week."""

    start: date
    lanes: int = Field(ge=0)
    bars: list[EventBar]
    days: list[CalendarDay]


class CalendarView(StrictModel):
    """Month-grid content in the data contract."""

    today: date
    weeks: list[CalendarWeek]


class DashboardData(StrictModel):
    """Top-level, versioned schema consumed by the display page."""

    schema_version: Literal[1]
    generated_at: AwareDatetime
    timezone: str
    theme: DashboardTheme
    status: DashboardStatus
    palette: dict[str, PaletteEntry]
    weather: WeatherSnapshot
    calendar: CalendarView

    @model_validator(mode="after")
    def calendar_colors_exist_in_palette(self) -> DashboardData:
        """Require event color references to resolve to serialized palette entries."""
        for week in self.calendar.weeks:
            for event in [*week.bars, *(chip for day in week.days for chip in day.chips)]:
                if event.calendar not in self.palette:
                    raise ValueError(
                        f"event '{event.id}' references missing palette key '{event.calendar}'"
                    )
        return self
