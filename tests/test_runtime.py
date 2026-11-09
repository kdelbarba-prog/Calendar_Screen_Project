"""Scheduler, cache recovery, source isolation, and HTTP health tests."""

from __future__ import annotations

import json
import random
import threading
from datetime import datetime, timedelta
from pathlib import Path
from urllib.request import urlopen
from zoneinfo import ZoneInfo

import pytest
from pydantic import SecretStr

from dashboard.config import CalendarConfig, load_config
from dashboard.models import (
    DashboardStatus,
    Event,
    SourceStatus,
)
from dashboard.services.fetch_once import empty_dashboard_data, fetch_once, save_state
from dashboard.services.runtime import DashboardRuntime, make_http_server
from dashboard.sources.base import CalendarSourceError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "sample_data.json"
CHICAGO = ZoneInfo("America/Chicago")
START = datetime(2026, 11, 8, 8, 0, tzinfo=CHICAGO)


@pytest.fixture
def config():
    """Load only the committed sample configuration."""
    return load_config(config_path=PROJECT_ROOT / "config" / "config.example.yaml")


@pytest.fixture
def sample_weather():
    """Load the known contract-valid weather fixture."""
    payload = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
    from dashboard.models import WeatherSnapshot

    return WeatherSnapshot.model_validate(payload["weather"])


def test_fetch_once_isolates_a_raising_calendar_from_other_sources(
    config,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sample_weather,
) -> None:
    """A broken calendar does not discard another calendar's successful events."""
    calendars = [
        CalendarConfig(
            name=name,
            url_env_var=f"{name.upper()}_URL",
            color=color,
            url=SecretStr(f"https://{name.casefold()}.example/calendar.ics"),
        )
        for name, color in (("Broken", "blue"), ("Working", "green"))
    ]
    config = config.model_copy(update={"calendars": calendars})
    statuses = {
        calendar.name: SourceStatus(
            id=calendar.url_env_var.lower(),
            name=calendar.name,
            kind="calendar",
            state="error",
            last_success=None,
            message=None,
        )
        for calendar in calendars
    }
    fail_working = False

    class FakeCalendarSource:
        def __init__(self, calendar_config, **_kwargs) -> None:
            self.config = calendar_config

        @property
        def status(self) -> SourceStatus:
            return statuses[self.config.name]

        def fetch_events(self, *, start, end, now):
            if self.config.name == "Broken" or (
                self.config.name == "Working" and fail_working
            ):
                raise RuntimeError("mock source failure")
            statuses["Working"] = statuses["Working"].model_copy(
                update={"state": "ok", "last_success": now}
            )
            return [
                Event(
                    calendar_name=self.config.name,
                    color=self.config.color,
                    title="Isolated success",
                    start=now.replace(hour=10),
                    end=now.replace(hour=11),
                    all_day=False,
                    location=None,
                    description_short=None,
                    status="normal",
                )
            ]

    monkeypatch.setattr(
        "dashboard.services.fetch_once.IcsCalendarSource",
        FakeCalendarSource,
    )
    monkeypatch.setattr(
        "dashboard.services.fetch_once.fetch_weather",
        lambda *_args, **_kwargs: sample_weather,
    )

    cache_path = tmp_path / "cache" / "state.json"
    result = fetch_once(config, now=START, cache_path=cache_path)

    visible_titles = [
        chip.title
        for week in result.calendar.weeks
        for day in week.days
        for chip in day.chips
    ]
    assert "Isolated success" in visible_titles
    assert {source.id: source.state for source in result.status.sources}[
        "broken_url"
    ] == "error"
    assert {source.id: source.state for source in result.status.sources}[
        "working_url"
    ] == "ok"

    fail_working = True
    recovered = fetch_once(
        config,
        now=START + timedelta(minutes=1),
        cache_path=cache_path,
    )
    recovered_titles = [
        chip.title
        for week in recovered.calendar.weeks
        for day in week.days
        for chip in day.chips
    ]
    assert "Isolated success" in recovered_titles


def test_scheduler_injected_clock_drives_offline_and_stale_transitions(
    config,
    tmp_path: Path,
) -> None:
    """Two failed connectivity checks set offline; data goes stale at 2x interval."""
    config = config.model_copy(
        update={
            "calendars": [],
            "weather": config.weather.model_copy(update={"refresh_minutes": 1}),
        }
    )
    data = empty_dashboard_data(config, now=START)
    weather_status = SourceStatus(
        id="weather",
        name="Weather",
        kind="weather",
        state="ok",
        last_success=START,
        message=None,
    )
    data = data.model_copy(
        update={
            "generated_at": START,
            "status": DashboardStatus(offline=False, sources=[weather_status]),
            "weather": data.weather.model_copy(update={"fetched_at": START}),
        }
    )
    connectivity_results = iter((False, False, True))
    runtime = DashboardRuntime(
        config,
        cache_path=tmp_path / "cache" / "state.json",
        output_path=tmp_path / "output",
        fetcher=lambda *_args, **_kwargs: data,
        renderer=lambda *_args, **_kwargs: data,
        connectivity=lambda: next(connectivity_results),
    )
    runtime.initialize(now=START)

    first = runtime.tick(now=START)
    second = runtime.tick(now=START + timedelta(minutes=1))
    third = runtime.tick(now=START + timedelta(minutes=2))

    assert first.status.offline is False
    assert second.status.offline is True
    assert third.status.offline is False
    assert third.status.sources[0].state == "stale"


def test_scheduler_refreshes_calendars_on_independent_intervals(
    config,
    tmp_path: Path,
) -> None:
    """Only sources whose individual interval elapsed are passed to fetch-once."""
    calendars = [
        CalendarConfig(
            name="Fast",
            url_env_var="FAST_URL",
            color="blue",
            refresh_minutes=1,
        ),
        CalendarConfig(
            name="Slow",
            url_env_var="SLOW_URL",
            color="green",
            refresh_minutes=5,
        ),
    ]
    config = config.model_copy(update={"calendars": calendars})
    data = empty_dashboard_data(config, now=START)
    calls: list[tuple[set[str] | None, bool]] = []

    def fake_fetcher(_config, **kwargs):
        calls.append((kwargs["calendar_ids"], kwargs["weather_due"]))
        return data

    runtime = DashboardRuntime(
        config,
        cache_path=tmp_path / "cache" / "state.json",
        output_path=tmp_path / "output",
        fetcher=fake_fetcher,
        renderer=lambda *_args, **_kwargs: data,
        connectivity=lambda: True,
        random_source=random.Random(1),
    )
    runtime.tick(now=START)
    runtime.tick(now=START + timedelta(minutes=1))

    assert calls == [
        ({"fast_url", "slow_url"}, True),
        ({"fast_url"}, False),
    ]


def test_corrupt_cache_is_backed_up_before_empty_state_is_rendered(
    config,
    tmp_path: Path,
) -> None:
    """Invalid cached JSON is moved to .bak and replaced with a valid empty state."""
    cache_path = tmp_path / "cache" / "state.json"
    cache_path.parent.mkdir()
    cache_path.write_text("{bad json", encoding="utf-8")
    runtime = DashboardRuntime(
        config,
        cache_path=cache_path,
        output_path=tmp_path / "output",
        renderer=lambda *_args, **_kwargs: empty_dashboard_data(config, now=START),
    )

    result = runtime.initialize(now=START)

    assert cache_path.with_name("state.json.bak").read_text(encoding="utf-8") == "{bad json"
    assert json.loads(cache_path.read_text(encoding="utf-8"))["schema_version"] == 1
    assert not cache_path.with_name("state.json.tmp").exists()
    assert result.calendar.today == START.date()


def test_atomic_state_write_removes_temp_file_when_replace_fails(
    config,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed atomic replacement preserves the destination and cleans its temp file."""
    cache_path = tmp_path / "state.json"
    cache_path.write_text("previous", encoding="utf-8")
    data = empty_dashboard_data(config, now=START)

    def fail_replace(_source: Path, _destination: Path) -> None:
        raise OSError("replacement failed")

    monkeypatch.setattr("dashboard.services.fetch_once.os.replace", fail_replace)
    with pytest.raises(OSError, match="replacement failed"):
        save_state(data, cache_path)

    assert cache_path.read_text(encoding="utf-8") == "previous"
    assert not cache_path.with_name("state.json.tmp").exists()


def test_healthz_http_response_contains_no_feed_urls(
    config,
    tmp_path: Path,
) -> None:
    """The live health route reports status without exposing configured feed URLs."""
    secret_url = "https://private.example/secret/calendar.ics?token=hidden"
    calendar = CalendarConfig(
        name="Private calendar",
        url_env_var="PRIVATE_CALENDAR_URL",
        color="blue",
        url=SecretStr(secret_url),
    )
    config = config.model_copy(update={"calendars": [calendar]})
    output_path = tmp_path / "output"
    (output_path / "static").mkdir(parents=True)
    (output_path / "data.json").write_text("{}", encoding="utf-8")
    (output_path / "static" / "dashboard.css").write_text("body {}", encoding="utf-8")
    runtime = DashboardRuntime(
        config,
        cache_path=tmp_path / "cache" / "state.json",
        output_path=output_path,
        renderer=lambda *_args, **_kwargs: empty_dashboard_data(config, now=START),
    )
    runtime.initialize(now=START)
    server = make_http_server("127.0.0.1", 0, output_path, runtime)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        with urlopen(f"http://127.0.0.1:{server.server_port}/healthz") as response:
            body = response.read().decode("utf-8")
        with urlopen(f"http://127.0.0.1:{server.server_port}/data.json") as response:
            data_cache_control = response.headers["Cache-Control"]
        with urlopen(
            f"http://127.0.0.1:{server.server_port}/static/dashboard.css"
        ) as response:
            static_cache_control = response.headers["Cache-Control"]
        payload = json.loads(body)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

    assert secret_url not in body
    assert "private.example" not in body
    assert payload["sources"][0]["id"] == "private_calendar_url"
    assert "last_update" in payload["sources"][0]
    assert "no-cache" in data_cache_control
    assert "max-age=31536000" in static_cache_control


def test_plain_http_calendar_urls_are_limited_to_loopback() -> None:
    """Local fixture servers are permitted while remote cleartext feeds are rejected."""
    from dashboard.sources.ics import _secret_url

    local = CalendarConfig(
        name="Local test",
        url_env_var="LOCAL_URL",
        color="blue",
        url=SecretStr("http://127.0.0.1:8000/calendar.ics"),
    )
    remote = local.model_copy(
        update={"url": SecretStr("http://calendar.example/calendar.ics")}
    )

    assert _secret_url(local) == "http://127.0.0.1:8000/calendar.ics"
    with pytest.raises(CalendarSourceError, match="only localhost"):
        _secret_url(remote)
