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


def test_week_heights_are_left_to_actual_browser_content_and_forecasts_are_in_headers(
    sample_data: DashboardData,
) -> None:
    """The browser sizes each row from content instead of using text estimates."""
    html = render_page(sample_data)

    assert html.count('class="week-grid"') == len(sample_data.calendar.weeks)
    assert "--week-min-height" not in html
    assert 'class="forecast-strip"' not in html
    assert 'class="day-forecast"' in html
    assert "Nov" in html
    assert '<time id="clock" class="clock">--:--:--</time>' in html


def test_active_day_omits_duplicate_daily_forecast(sample_data: DashboardData) -> None:
    """The larger current/today weather summary replaces the active date's forecast."""
    html = render_page(sample_data)
    today_cell = html.split('<div class="date-cell is-today"', 1)[1].split(
        '<div class="date-cell', 1
    )[0]
    script = (PROJECT_ROOT / "static" / "dashboard.js").read_text(encoding="utf-8")

    assert 'class="day-forecast"' not in today_cell
    assert 'class="day-forecast"' in html
    assert "if (forecast && !day.is_today)" in script


def test_week_rows_use_intrinsic_content_height_and_resize_independently() -> None:
    """Week rows grow to the real chip stack and are fitted using DOM measurements."""
    css = (PROJECT_ROOT / "static" / "dashboard.css").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "static" / "dashboard.js").read_text(encoding="utf-8")

    assert "flex: 0 0 auto" in css
    assert "grid-template-rows: 44px repeat(var(--lanes), 34px) max-content" in css
    assert "min-height: max-content" in css
    assert "getBoundingClientRect().height" in script
    assert "new ResizeObserver(fitRenderedWeeks)" in script
    assert "estimatedWeekHeight" not in script
    assert "estimatedChipHeight" not in script


def test_weather_and_date_header_alignment_styles() -> None:
    """Current conditions stack above today's summary on the right; headers read separately."""
    css = (PROJECT_ROOT / "static" / "dashboard.css").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "static" / "dashboard.js").read_text(encoding="utf-8")

    weather_styles = css.split(".weather-panel {", 1)[1].split("}", 1)[0]
    date_styles = css.split(".date-cell {", 1)[1].split("}", 1)[0]
    forecast_styles = css.split(".day-forecast {", 1)[1].split("}", 1)[0]

    assert "align-items: flex-end" in weather_styles
    assert "weatherPanel.append(currentRow, summary)" in script
    assert "background: #202a34" in date_styles
    assert "padding-right: 6px" in forecast_styles


def test_weekday_header_and_date_dividers_extend_through_forecast_row(
    sample_data: DashboardData,
) -> None:
    """Weekday initials align with every date, while today is marked only by its circle."""
    html = render_page(sample_data)
    css = (PROJECT_ROOT / "static" / "dashboard.css").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "static" / "dashboard.js").read_text(encoding="utf-8")

    weekday_row = html.split('<div class="weekday-row"', 1)[1].split("</div>", 1)[0]
    assert [label for label in "SMTWTFS"] == [
        item.split("</span>", 1)[0].split(">")[-1]
        for item in weekday_row.split('<span class="weekday-label">')[1:]
    ]
    assert ".date-cell.is-today {" not in css
    assert ".date-cell.is-today .date-number {" in css
    assert "border-right: 1px solid #44515c" in css
    assert "querySelector(\".weekday-row\")" in script
    assert "const background = element(\"div\", \"day-background\")" in script
    week_styles = css.split(".week-grid {", 1)[1].split("}", 1)[0]
    assert "padding: 0 0 8px" in week_styles


def test_chip_detail_text_wraps_instead_of_ellipsis_clipping() -> None:
    """Calendar card titles and locations are allowed to wrap onto more lines."""
    css = (PROJECT_ROOT / "static" / "dashboard.css").read_text(encoding="utf-8")
    title_styles = css.split(".chip-title,\n.chip-location {", 1)[1].split("}", 1)[0]

    assert "white-space: normal" in title_styles
    assert "overflow-wrap: anywhere" in title_styles
    assert "text-overflow: ellipsis" not in title_styles


def test_stale_overfull_calendar_payload_reaches_browser_for_actual_fit(
    sample_data: DashboardData,
) -> None:
    """Cached week lists are passed whole to the runtime DOM fitter."""
    calendar = sample_data.calendar.model_copy(
        update={"weeks": [sample_data.calendar.weeks[0]] * 6}
    )
    overfull_data = sample_data.model_copy(update={"calendar": calendar})
    html = render_page(overfull_data)

    assert html.count('class="week-grid"') == 6
    script = (PROJECT_ROOT / "static" / "dashboard.js").read_text(encoding="utf-8")
    assert "calendar.weeks.forEach((week) => {" in script
    assert "weeks.slice(visibleCount).forEach((week) => week.classList.add(\"is-hidden\"))" in script


def test_clock_script_updates_seconds_without_page_reload() -> None:
    """The displayed seconds are updated by the clock interval in JavaScript."""
    script = (PROJECT_ROOT / "static" / "dashboard.js").read_text(encoding="utf-8")

    assert 'second: "2-digit"' in script
    assert "window.setInterval(updateClock, 1000)" in script
    assert "window.location.reload" not in script


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
