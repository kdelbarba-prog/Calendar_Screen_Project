"""YAML configuration models and safe loading helpers."""

from __future__ import annotations

import os
import re
from datetime import time
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from dotenv import load_dotenv
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    ValidationInfo,
    field_validator,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIRECTORY = PROJECT_ROOT / "config"


class ConfigError(ValueError):
    """Raised when a dashboard configuration file is invalid or incomplete."""


class StrictModel(BaseModel):
    """Base model that rejects misspelled or unsupported configuration fields."""

    model_config = ConfigDict(extra="forbid")


class NightThemeConfig(StrictModel):
    """Night-theme display settings."""

    enabled: bool = True
    start: str = "21:00"
    end: str = "06:30"

    @field_validator("start", "end")
    @classmethod
    def validate_time(cls, value: str) -> str:
        """Require an ISO 24-hour time."""
        try:
            time.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("must be a valid 24-hour time such as '21:00'") from exc
        return value


class DisplayConfig(StrictModel):
    """Clock and timezone settings."""

    timezone: str = "America/Chicago"
    time_format: Literal["12h", "24h"] = "12h"
    clock_show_seconds: bool = True
    night_theme: NightThemeConfig = Field(default_factory=NightThemeConfig)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        """Require a timezone available through zoneinfo."""
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("must be a valid IANA timezone") from exc
        return value


class CalendarConfig(StrictModel):
    """Settings for one ICS calendar source."""

    name: str = Field(min_length=1)
    type: Literal["ics"] = "ics"
    url_env_var: str = Field(min_length=1)
    color: str = Field(min_length=1)
    enabled: bool = True
    refresh_minutes: int = Field(default=5, ge=1, le=1440)
    my_email: str | None = None
    show_declined: bool = False
    show_tentative: bool = True
    url: SecretStr | None = Field(default=None, exclude=True, repr=False)


class CalendarViewConfig(StrictModel):
    """Layout budgets for the calendar grid."""

    min_weeks: int = Field(default=2, ge=1, le=6)
    max_weeks: int = Field(default=6, ge=1, le=6)
    line_budget: int = Field(
        default=8,
        ge=1,
        description="Legacy compatibility setting; week fitting uses the rendered viewport height.",
    )
    max_events_per_day: int = Field(default=6, ge=1)
    max_bar_lanes: int = Field(default=3, ge=1)
    max_title_chars: int = Field(default=32, ge=1)
    max_lines_per_event: int = Field(default=2, ge=1)
    location_mode: Literal["city", "full", "none"] = "city"

    @field_validator("max_weeks")
    @classmethod
    def max_weeks_not_below_minimum(cls, value: int, info: ValidationInfo) -> int:
        """Ensure the calendar can fit at least its configured minimum week count."""
        minimum = info.data.get("min_weeks", 2)
        if value < minimum:
            raise ValueError("must be greater than or equal to min_weeks")
        return value


class WeatherConfig(StrictModel):
    """Weather provider and location settings."""

    provider: Literal["nws", "openmeteo"] = "nws"
    fallback_provider: Literal["nws", "openmeteo"] | None = "openmeteo"
    nws_station: str | None = None
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    units: Literal["imperial", "metric"] = "imperial"
    refresh_minutes: int = Field(default=30, ge=1, le=1440)
    user_agent: str = Field(min_length=1)


class BackgroundConfig(StrictModel):
    """Rotating background image settings."""

    folder: str = "backgrounds"
    change_minutes: int = Field(default=60, ge=1)
    enabled: bool = True


class ServerConfig(StrictModel):
    """Local HTTP server binding."""

    host: str = "127.0.0.1"
    port: int = Field(default=8080, ge=1, le=65535)


class DashboardConfig(StrictModel):
    """Validated top-level dashboard configuration."""

    display: DisplayConfig = Field(default_factory=DisplayConfig)
    calendars: list[CalendarConfig] = Field(default_factory=list)
    calendar_view: CalendarViewConfig = Field(default_factory=CalendarViewConfig)
    weather: WeatherConfig
    backgrounds: BackgroundConfig = Field(default_factory=BackgroundConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)


class PaletteColor(StrictModel):
    """One named palette color, including its tentative-state treatment."""

    bg: str
    fg: str
    tentative_bg: str
    tentative_fg: str

    @field_validator("bg", "fg", "tentative_bg", "tentative_fg")
    @classmethod
    def validate_hex_color(cls, value: str) -> str:
        """Require six-digit hexadecimal CSS colors."""
        if re.fullmatch(r"#[0-9A-Fa-f]{6}", value) is None:
            raise ValueError("must be a six-digit hexadecimal color such as '#1F4E8C'")
        return value.upper()


def _format_validation_error(error: ValidationError) -> str:
    """Format Pydantic errors without including submitted values or secrets."""
    details = []
    for item in error.errors(include_input=False):
        location = ".".join(str(part) for part in item["loc"]) or "configuration"
        details.append(f"{location}: {item['msg']}")
    return "; ".join(details)


def _read_yaml(path: Path, label: str) -> object:
    """Read YAML while keeping parse errors concise and free of input values."""
    try:
        with path.open(encoding="utf-8") as file:
            return yaml.safe_load(file)
    except OSError as exc:
        raise ConfigError(f"Unable to read {label} file '{path}': {exc.strerror}") from exc
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        position = f" at line {mark.line + 1}" if mark is not None else ""
        raise ConfigError(f"Invalid YAML in {label} file '{path}'{position}.") from exc


def load_palette(path: Path | str | None = None) -> dict[str, PaletteColor]:
    """Load and validate the named color palette."""
    palette_path = Path(path) if path is not None else CONFIG_DIRECTORY / "colors.yaml"
    raw_palette = _read_yaml(palette_path, "palette")
    if not isinstance(raw_palette, dict) or not raw_palette:
        raise ConfigError(f"Palette file '{palette_path}' must contain named colors.")
    try:
        return {
            str(name): PaletteColor.model_validate(values)
            for name, values in raw_palette.items()
        }
    except ValidationError as exc:
        raise ConfigError(f"Invalid palette in '{palette_path}': {_format_validation_error(exc)}") from exc


def load_config(
    config_path: Path | str | None = None,
    colors_path: Path | str | None = None,
    env_path: Path | str | None = None,
) -> DashboardConfig:
    """Load YAML configuration, validate colors, and resolve feed URLs from dotenv."""
    if config_path is None:
        private_config = CONFIG_DIRECTORY / "config.yaml"
        selected_config = private_config if private_config.exists() else CONFIG_DIRECTORY / "config.example.yaml"
    else:
        selected_config = Path(config_path)
    palette = load_palette(colors_path)
    raw_config = _read_yaml(selected_config, "configuration")
    try:
        config = DashboardConfig.model_validate(raw_config)
    except ValidationError as exc:
        raise ConfigError(
            f"Invalid configuration in '{selected_config}': {_format_validation_error(exc)}"
        ) from exc

    for index, calendar in enumerate(config.calendars):
        if calendar.color not in palette:
            raise ConfigError(
                f"calendars[{index}].color references unknown palette name '{calendar.color}'."
            )

    dotenv_path = Path(env_path) if env_path is not None else PROJECT_ROOT / ".env"
    load_dotenv(dotenv_path=dotenv_path, override=False)
    resolved_calendars = []
    for index, calendar in enumerate(config.calendars):
        feed_url = os.environ.get(calendar.url_env_var)
        if calendar.enabled and not feed_url:
            raise ConfigError(
                f"calendars[{index}] '{calendar.name}' requires environment variable "
                f"{calendar.url_env_var!r} in '{dotenv_path}'."
            )
        resolved_calendars.append(
            calendar.model_copy(update={"url": SecretStr(feed_url) if feed_url else None})
        )

    return config.model_copy(update={"calendars": resolved_calendars})
