"""Tests for the initial config and serialized data contract."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from dashboard.config import ConfigError, load_config, load_palette
from dashboard.__main__ import main as dashboard_main
from dashboard.models import DashboardData

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_CONFIG = PROJECT_ROOT / "config" / "config.example.yaml"
COLORS_FILE = PROJECT_ROOT / "config" / "colors.yaml"
SAMPLE_DATA = PROJECT_ROOT / "tests" / "fixtures" / "sample_data.json"


def _config_with_calendar(*, refresh_minutes: int, color: str) -> str:
    """Build a minimal calendar config for targeted validation cases."""
    return f"""
weather:
  lat: 41.8781
  lon: -87.6298
  user_agent: test-agent
calendars:
  - name: Test calendar
    url_env_var: TEST_CALENDAR_URL
    color: {color}
    refresh_minutes: {refresh_minutes}
"""


def _relative_luminance(color: str) -> float:
    """Calculate WCAG relative luminance for a six-digit sRGB color."""
    channels = [int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [
        channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
        for channel in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast_ratio(first: str, second: str) -> float:
    """Return the WCAG contrast ratio between two sRGB colors."""
    light, dark = sorted((_relative_luminance(first), _relative_luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def test_example_config_loads() -> None:
    """The committed example is valid and does not require feed secrets."""
    config = load_config(config_path=EXAMPLE_CONFIG)
    assert len(config.calendars) == 1
    assert config.calendars[0].enabled is False
    assert config.calendars[0].refresh_minutes == 5
    assert config.display.timezone == "America/Chicago"


def test_check_config_returns_one_for_invalid_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """The config-check command reports invalid settings with exit status one."""
    def fail_load_config() -> None:
        raise ConfigError("invalid test configuration")

    monkeypatch.setattr("dashboard.__main__.load_config", fail_load_config)
    monkeypatch.setattr(sys, "argv", ["dashboard", "--check-config"])

    assert dashboard_main() == 1


def test_calendar_refresh_minutes_must_be_in_range(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject refresh intervals below the documented minimum."""
    monkeypatch.setenv("TEST_CALENDAR_URL", "https://calendar.invalid/private.ics")
    path = tmp_path / "config.yaml"
    path.write_text(_config_with_calendar(refresh_minutes=0, color="blue"), encoding="utf-8")

    with pytest.raises(ConfigError, match=r"refresh_minutes"):
        load_config(config_path=path, colors_path=COLORS_FILE, env_path=tmp_path / ".env")


def test_unknown_palette_name_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject calendar color names absent from the committed palette."""
    monkeypatch.setenv("TEST_CALENDAR_URL", "https://calendar.invalid/private.ics")
    path = tmp_path / "config.yaml"
    path.write_text(_config_with_calendar(refresh_minutes=5, color="ultraviolet"), encoding="utf-8")

    with pytest.raises(ConfigError, match="unknown palette name 'ultraviolet'"):
        load_config(config_path=path, colors_path=COLORS_FILE, env_path=tmp_path / ".env")


def test_enabled_calendar_requires_dotenv_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Require the configured URL variable without exposing its value."""
    monkeypatch.delenv("TEST_CALENDAR_URL", raising=False)
    path = tmp_path / "config.yaml"
    path.write_text(_config_with_calendar(refresh_minutes=5, color="blue"), encoding="utf-8")

    with pytest.raises(ConfigError, match="requires environment variable 'TEST_CALENDAR_URL'"):
        load_config(config_path=path, colors_path=COLORS_FILE, env_path=tmp_path / ".env")


def test_all_palette_color_pairs_meet_wcag_aa() -> None:
    """Every normal and tentative text/background pair has at least 4.5:1 contrast."""
    for name, color in load_palette(COLORS_FILE).items():
        assert _contrast_ratio(color.fg, color.bg) >= 4.5, name
        assert _contrast_ratio(color.tentative_fg, color.tentative_bg) >= 4.5, name


def test_yellow_palette_is_available_for_calendars() -> None:
    """The named yellow calendar color is included in the validated palette."""
    palette = load_palette(COLORS_FILE)

    assert "yellow" in palette
    assert _contrast_ratio(palette["yellow"].fg, palette["yellow"].bg) >= 4.5
    assert _contrast_ratio(palette["yellow"].tentative_fg, palette["yellow"].tentative_bg) >= 4.5


def test_handwritten_sample_validates_against_dashboard_data() -> None:
    """The fixture demonstrates the current versioned page data contract."""
    data = DashboardData.model_validate_json(SAMPLE_DATA.read_text(encoding="utf-8"))
    assert len(data.weather.daily) == 7
    assert any(source.state == "stale" for source in data.status.sources)


def test_dashboard_data_rejects_bad_event_status() -> None:
    """Reject event-state values outside the published contract."""
    sample = json.loads(SAMPLE_DATA.read_text(encoding="utf-8"))
    sample["calendar"]["weeks"][0]["bars"][0]["status"] = "unknown"

    with pytest.raises(ValidationError):
        DashboardData.model_validate(sample)


def test_dashboard_data_rejects_palette_references_without_colors() -> None:
    """Calendar events may reference only colors included in the data palette."""
    sample = json.loads(SAMPLE_DATA.read_text(encoding="utf-8"))
    del sample["palette"]["blue"]

    with pytest.raises(ValidationError, match="missing palette key 'blue'"):
        DashboardData.model_validate(sample)
