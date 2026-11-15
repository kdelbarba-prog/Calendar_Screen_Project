"""Dashboard template rendering, static output, and performance-budget checks."""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Literal

from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import ValidationError

from dashboard.config import PROJECT_ROOT
from dashboard.models import DashboardData
from dashboard.services.backgrounds import prepare_backgrounds

logger = logging.getLogger(__name__)
TEMPLATES_DIRECTORY = PROJECT_ROOT / "templates"
STATIC_DIRECTORY = PROJECT_ROOT / "static"
BACKGROUND_MAX_BYTES = 1_000_000
PAGE_MAX_BYTES = 300 * 1024
RenderScenario = Literal["normal", "busy", "empty", "long-titles", "stale", "offline", "night"]


class RenderError(RuntimeError):
    """A safe, actionable error while validating or rendering dashboard data."""


def render_page(data: DashboardData | dict[str, Any]) -> str:
    """Render one HTML page; optional weather/calendar sections may be omitted."""
    if isinstance(data, DashboardData):
        context = data.model_dump(mode="json")
    else:
        context = dict(data)
    environment = Environment(
        loader=FileSystemLoader(TEMPLATES_DIRECTORY),
        autoescape=select_autoescape(("html", "xml")),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    try:
        template = environment.get_template("index.html")
        return template.render(data=context)
    except Exception as exc:
        logger.error("Dashboard template rendering failed (%s).", type(exc).__name__)
        raise RenderError("Dashboard page could not be rendered; check the template.") from None


def apply_scenario(data: DashboardData, scenario: RenderScenario) -> DashboardData:
    """Return a validated in-memory fixture mutation for visual QA scenarios."""
    payload = data.model_dump(mode="python")
    if scenario == "normal":
        return data
    if scenario == "empty":
        payload["calendar"]["weeks"] = [
            {
                **week,
                "lanes": 0,
                "bars": [],
                "days": [
                    {**day, "chips": [], "more_count": 0}
                    for day in week["days"]
                ],
            }
            for week in payload["calendar"]["weeks"]
        ]
    elif scenario == "busy":
        for week in payload["calendar"]["weeks"]:
            for day in week["days"]:
                day["chips"] = list(day["chips"])
                if day["date"] == payload["calendar"]["today"]:
                    base_chips = list(day["chips"])
                    while len(day["chips"]) < 10:
                        source = base_chips[len(day["chips"]) % len(base_chips)] if base_chips else None
                        if source is None:
                            source = {
                                "id": "busy-event",
                                "title": "Busy scenario event",
                                "time_label": "9:00a",
                                "location": None,
                                "calendar": next(iter(payload["palette"]), "blue"),
                                "status": "normal",
                            }
                        day["chips"].append(
                            {
                                **source,
                                "id": f"busy-{len(day['chips'])}",
                                "title": f"Busy scenario event {len(day['chips']) + 1}",
                                "status": "normal",
                            }
                        )
                    day["more_count"] = max(int(day["more_count"]), len(day["chips"]) - 6)
                    break
    elif scenario == "long-titles":
        for week in payload["calendar"]["weeks"]:
            for bar in week["bars"]:
                bar["title"] = "A deliberately very long calendar event title for renderer stress testing"
            for day in week["days"]:
                for chip in day["chips"]:
                    chip["title"] = "A deliberately very long calendar chip title for renderer stress testing"
    elif scenario == "stale":
        for source in payload["status"]["sources"]:
            source["state"] = "stale"
            source["message"] = "Using the last successful source data."
    elif scenario == "offline":
        payload["status"]["offline"] = True
        for source in payload["status"]["sources"]:
            source["state"] = "stale"
            source["message"] = "Using the last successful source data."
    elif scenario == "night":
        payload["theme"]["night_enabled"] = True
        payload["theme"]["night_start"] = "00:00"
        payload["theme"]["night_end"] = "23:59"
    else:
        raise ValueError(f"Unknown render scenario: {scenario}")
    return DashboardData.model_validate(payload)


def render_output(
    state_path: Path | str,
    output_path: Path | str,
    *,
    backgrounds_path: Path | str | None = None,
    backgrounds_enabled: bool = True,
    change_minutes: int = 60,
) -> DashboardData:
    """Validate cached JSON, render index/data files, and copy local static assets."""
    state_file = Path(state_path)
    output_directory = Path(output_path)
    try:
        payload = json.loads(state_file.read_text(encoding="utf-8"))
        data = DashboardData.model_validate(payload)
    except OSError as exc:
        raise RenderError(f"Unable to read dashboard state '{state_file}': {exc.strerror}") from exc
    except (json.JSONDecodeError, ValidationError) as exc:
        if isinstance(exc, ValidationError):
            errors = "; ".join(
                f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
                for error in exc.errors(include_input=False)
            )
            raise RenderError(f"Dashboard state does not match the data contract: {errors}") from None
        raise RenderError(f"Dashboard state '{state_file}' is not valid JSON.") from None

    html = render_page(data)
    output_directory.mkdir(parents=True, exist_ok=True)
    _atomic_write(output_directory / "index.html", html.encode("utf-8"))
    _atomic_write(
        output_directory / "data.json",
        (data.model_dump_json(indent=2) + "\n").encode("utf-8"),
    )
    _copy_static(output_directory)

    prepared_backgrounds: list[str] = []
    if backgrounds_path is not None:
        prepared_backgrounds = prepare_backgrounds(
            Path(backgrounds_path),
            output_directory / "backgrounds",
            enabled=backgrounds_enabled,
        )
    client_config = {
        "timezone": data.timezone,
        "backgrounds": prepared_backgrounds,
        "backgroundChangeMinutes": max(1, change_minutes),
    }
    script = (
        "\n<script>window.DASHBOARD_CONFIG="
        + json.dumps(client_config, ensure_ascii=True, separators=(",", ":"))
        + ";</script>\n"
    )
    index_path = output_directory / "index.html"
    rendered = index_path.read_text(encoding="utf-8")
    if "</body>" not in rendered:
        raise RenderError("Dashboard template is missing its closing body tag.")
    _atomic_write(
        index_path,
        rendered.replace("</body>", script + "</body>").encode("utf-8"),
    )
    return data


def _atomic_write(path: Path, content: bytes) -> None:
    """Write a file to a sibling temporary path and atomically replace its destination."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(path.name + ".tmp")
    try:
        with temporary_path.open("wb") as file:
            file.write(content)
            file.flush()
        os.replace(temporary_path, path)
    except OSError:
        temporary_path.unlink(missing_ok=True)
        raise


def _copy_static(output_directory: Path) -> None:
    """Copy project static assets into output without copying output state recursively."""
    target = output_directory / "static"
    if target.exists():
        shutil.rmtree(target)
    if STATIC_DIRECTORY.is_dir():
        shutil.copytree(STATIC_DIRECTORY, target)


def check_budget(output_path: Path | str) -> tuple[bool, list[str]]:
    """Check page/static size and prepared JPEG size against display budgets."""
    output_directory = Path(output_path)
    if not output_directory.is_dir():
        return False, [f"Output directory does not exist: {output_directory}"]
    page_bytes = sum(
        item.stat().st_size
        for item in output_directory.rglob("*")
        if item.is_file() and "backgrounds" not in item.relative_to(output_directory).parts
    )
    issues = []
    if page_bytes > PAGE_MAX_BYTES:
        issues.append(f"Page and static assets total {page_bytes} bytes (limit {PAGE_MAX_BYTES}).")
    for background in (output_directory / "backgrounds").glob("*.jpg"):
        size = background.stat().st_size
        if size > BACKGROUND_MAX_BYTES:
            issues.append(
                f"Background '{background.name}' is {size} bytes (limit {BACKGROUND_MAX_BYTES})."
            )
    return not issues, issues
