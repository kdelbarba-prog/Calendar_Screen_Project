"""Fixture-backed renderer, scenario, output-budget, and one-shot cache tests."""

from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from PIL import Image

from dashboard.config import load_config
from dashboard.models import DashboardData, WeatherSnapshot
from dashboard.renderer import (
    RenderError,
    RenderScenario,
    apply_scenario,
    check_budget,
    render_output,
    render_page,
)
from dashboard.services.fetch_once import fetch_once

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "sample_data.json"
CHICAGO = ZoneInfo("America/Chicago")
NOW = datetime(2026, 11, 7, 9, 40, tzinfo=CHICAGO)
SCENARIOS: tuple[RenderScenario, ...] = (
    "normal",
    "busy",
    "empty",
    "long-titles",
    "stale",
    "offline",
    "night",
)


@pytest.fixture
def sample_data() -> DashboardData:
    """Load and validate the committed sample data."""
    return DashboardData.model_validate_json(SAMPLE_PATH.read_text(encoding="utf-8"))


def test_template_renders_sample_bars_chips_status_classes_and_palette(
    sample_data: DashboardData,
) -> None:
    """Server-rendered HTML includes calendar rows and event state styling."""
    html = render_page(sample_data)

    assert 'class="event-bar status-normal' in html
    assert 'class="event-chip status-tentative' in html
    assert 'class="event-chip status-declined' in html
    assert "Conference" in html
    assert "Soccer practice" in html
    assert "+3 more" in html
    assert "--chip-bg: #C6D9F1" in html
    assert "continues-right" in html


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_each_sample_scenario_still_validates(
    sample_data: DashboardData,
    scenario: RenderScenario,
) -> None:
    """All visual QA mutations remain within the DashboardData contract."""
    changed = apply_scenario(sample_data, scenario)
    validated = DashboardData.model_validate(changed.model_dump(mode="python"))

    assert validated.schema_version == 1
    if scenario == "offline":
        assert validated.status.offline is True
    elif scenario == "stale":
        assert all(source.state == "stale" for source in validated.status.sources)
    elif scenario == "empty":
        assert all(not week.bars and all(not day.chips for day in week.days) for week in validated.calendar.weeks)


def test_optional_missing_weather_or_calendar_renders() -> None:
    """The Jinja template remains usable for a partial startup/preview payload."""
    html = render_page(
        {
            "schema_version": 1,
            "timezone": "America/Chicago",
            "status": {"offline": True},
            "weather": None,
            "calendar": None,
        }
    )

    assert "Weather unavailable" in html
    assert "No calendar events" in html
    assert "Offline" in html


def test_render_output_writes_contract_json_page_and_local_assets(
    tmp_path: Path,
    sample_data: DashboardData,
) -> None:
    """Render output includes data.json, index.html, and copied vanilla static assets."""
    state_path = tmp_path / "state.json"
    output_path = tmp_path / "output"
    state_path.write_text(sample_data.model_dump_json(), encoding="utf-8")

    rendered = render_output(state_path, output_path, backgrounds_enabled=False)

    assert rendered == sample_data
    assert DashboardData.model_validate_json((output_path / "data.json").read_text()) == sample_data
    html = (output_path / "index.html").read_text(encoding="utf-8")
    assert "<!doctype html>" in html
    assert "window.DASHBOARD_CONFIG" in html
    assert (output_path / "static" / "dashboard.css").is_file()
    assert (output_path / "static" / "dashboard.js").is_file()
    assert (output_path / "static" / "icons" / "rain.svg").is_file()


def test_backgrounds_are_resized_to_top_zone_and_budget_excludes_them(
    tmp_path: Path,
) -> None:
    """Local source images are pre-resized and excluded from the page-byte budget."""
    source_folder = tmp_path / "source-backgrounds"
    source_folder.mkdir()
    Image.new("RGB", (2400, 1600), color="#334455").save(source_folder / "wall.png")
    output_path = tmp_path / "output"
    state_path = tmp_path / "state.json"
    data = DashboardData.model_validate_json(SAMPLE_PATH.read_text(encoding="utf-8"))
    state_path.write_text(data.model_dump_json(), encoding="utf-8")

    render_output(
        state_path,
        output_path,
        backgrounds_path=source_folder,
        backgrounds_enabled=True,
    )

    background_paths = list((output_path / "backgrounds").glob("*.jpg"))
    assert len(background_paths) == 1
    with Image.open(background_paths[0]) as resized:
        assert resized.size == (1080, 620)
    passed, issues = check_budget(output_path)
    assert passed, issues


def test_check_budget_rejects_oversized_page_asset(tmp_path: Path) -> None:
    """The page byte limit is enforced on files outside the backgrounds directory."""
    output_path = tmp_path / "output"
    output_path.mkdir()
    (output_path / "large.css").write_bytes(b"x" * (300 * 1024 + 1))

    passed, issues = check_budget(output_path)

    assert passed is False
    assert any("limit" in issue for issue in issues)


def test_renderer_reports_contract_errors(tmp_path: Path) -> None:
    """Invalid contract input fails explicitly with a safe render error."""
    invalid_state = tmp_path / "bad.json"
    invalid_state.write_text('{"schema_version": 100}', encoding="utf-8")

    with pytest.raises(RenderError, match="does not match the data contract"):
        render_output(invalid_state, tmp_path / "output")


def test_renderer_reports_template_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing template is reported as a render error rather than swallowed."""
    monkeypatch.setattr("dashboard.renderer.TEMPLATES_DIRECTORY", tmp_path)

    with pytest.raises(RenderError, match="could not be rendered"):
        render_page({"timezone": "UTC"})


def test_fetch_once_writes_valid_state_atomically_without_live_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample_data: DashboardData,
) -> None:
    """The one-shot runner persists a contract snapshot and leaves no temp file."""
    config = load_config(config_path=PROJECT_ROOT / "config" / "config.example.yaml")
    previous_weather: WeatherSnapshot = sample_data.weather
    monkeypatch.setattr(
        "dashboard.services.fetch_once.fetch_weather",
        lambda *_args, **_kwargs: previous_weather,
    )
    cache_path = tmp_path / "cache" / "state.json"

    data = fetch_once(config, now=NOW, cache_path=cache_path)

    assert DashboardData.model_validate_json(cache_path.read_text(encoding="utf-8")) == data
    assert not cache_path.with_name("state.json.tmp").exists()
    assert data.calendar.today == NOW.date()
    assert data.weather == previous_weather


def test_scenario_cli_does_not_create_persistent_scenario_fixtures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Sample scenarios are applied in memory and do not write extra fixture JSON."""
    from dashboard.__main__ import main

    fixture_path = tmp_path / "tests" / "fixtures" / "sample_data.json"
    fixture_path.parent.mkdir(parents=True)
    shutil.copyfile(SAMPLE_PATH, fixture_path)

    monkeypatch.setattr(
        sys,
        "argv",
        ["dashboard", "render", "--sample", "--scenario", "night"],
    )
    monkeypatch.setattr("dashboard.__main__.PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        "dashboard.__main__.CONFIG_DIRECTORY",
        PROJECT_ROOT / "config",
    )

    assert main() == 0
    rendered = DashboardData.model_validate_json(
        (tmp_path / "output" / "data.json").read_text(encoding="utf-8")
    )
    assert rendered.theme.night_start == "00:00"
    assert not (tmp_path / "cache").exists()
