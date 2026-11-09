"""Scheduler and local HTTP runtime for the wall display."""

from __future__ import annotations

import ctypes
import json
import logging
import os
import platform
import random
import re
import signal
import socket
import threading
import time
from datetime import datetime, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from dashboard.config import DashboardConfig, PROJECT_ROOT
from dashboard.models import DashboardData, DashboardStatus, SourceStatus
from dashboard.renderer import RenderError, render_output
from dashboard.services.fetch_once import (
    DEFAULT_CACHE_PATH,
    empty_dashboard_data,
    fetch_once,
    save_state,
)

logger = logging.getLogger(__name__)
CONNECTIVITY_INTERVAL = timedelta(seconds=60)
CONNECTIVITY_TIMEOUT_SECONDS = 2.0
METRICS_INTERVAL = timedelta(hours=1)
JITTER_FRACTION = 0.1


def check_connectivity() -> bool:
    """Perform a lightweight outbound TCP check without making an HTTP request."""
    try:
        with socket.create_connection(
            ("1.1.1.1", 443),
            timeout=CONNECTIVITY_TIMEOUT_SECONDS,
        ):
            return True
    except OSError:
        return False


class DashboardRuntime:
    """Run source refreshes, health transitions, and cached-page rendering."""

    def __init__(
        self,
        config: DashboardConfig,
        *,
        cache_path: Path | str = DEFAULT_CACHE_PATH,
        output_path: Path | str = PROJECT_ROOT / "output",
        fetcher: Callable[..., DashboardData] = fetch_once,
        renderer: Callable[..., DashboardData] = render_output,
        connectivity: Callable[[], bool] = check_connectivity,
        random_source: random.Random | None = None,
    ) -> None:
        """Create a runtime with injectable I/O functions for deterministic tests."""
        self.config = config
        self.cache_path = Path(cache_path)
        self.output_path = Path(output_path)
        self.source_cache_path = self.cache_path.parent / "sources"
        self.fetcher = fetcher
        self.renderer = renderer
        self.connectivity = connectivity
        self.random = random_source or random.Random()
        self._data_lock = threading.Lock()
        self._data: DashboardData | None = None
        self._next_due: dict[str, datetime] = {}
        self._next_connectivity: datetime | None = None
        self._connectivity_failures = 0
        self.offline = False
        self._last_metrics = None
        self._fetch_duration = 0.0
        self._render_duration = 0.0

    @property
    def data(self) -> DashboardData:
        """Return the latest published snapshot."""
        with self._data_lock:
            if self._data is None:
                raise RuntimeError("Dashboard runtime has not been initialized.")
            return self._data

    def initialize(self, *, now: datetime) -> DashboardData:
        """Recover cache if needed and render its last-known-good state immediately."""
        cached: DashboardData | None = None
        try:
            cached = DashboardData.model_validate_json(
                self.cache_path.read_text(encoding="utf-8")
            )
        except FileNotFoundError:
            pass
        except (OSError, ValidationError, ValueError):
            backup = self.cache_path.with_name(self.cache_path.name + ".bak")
            try:
                os.replace(self.cache_path, backup)
            except OSError:
                logger.exception("Could not move corrupt dashboard cache to its backup.")
                raise
            logger.warning("Corrupt dashboard cache moved to state.json.bak; starting empty.")

        if cached is None:
            cached = empty_dashboard_data(self.config, now=now)
        self.offline = cached.status.offline
        cached = self._apply_health(cached, now)
        save_state(cached, self.cache_path)
        self._publish(cached)
        self._render()
        self._last_metrics = now
        return cached

    def tick(self, *, now: datetime) -> DashboardData:
        """Run due connectivity/source checks and publish a fresh rendered snapshot."""
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        local_now = now.astimezone(ZoneInfo(self.config.display.timezone))
        if self._data is None:
            self.initialize(now=local_now)

        health_changed = False
        if self._next_connectivity is None or local_now >= self._next_connectivity:
            previous_offline = self.offline
            self._check_connectivity()
            health_changed = previous_offline != self.offline
            self._next_connectivity = local_now + CONNECTIVITY_INTERVAL

        due_calendar_ids: set[str] = set()
        for calendar in self.config.calendars:
            if not calendar.enabled:
                continue
            source_id = calendar.url_env_var.lower()
            if self._is_due(source_id, local_now):
                due_calendar_ids.add(source_id)
                self._schedule_next(source_id, local_now, calendar.refresh_minutes)
        weather_due = self._is_due("weather", local_now)
        if weather_due:
            self._schedule_next("weather", local_now, self.config.weather.refresh_minutes)

        fetch_duration = 0.0
        render_duration = 0.0
        did_fetch = bool(due_calendar_ids) or weather_due
        if did_fetch:
            fetch_started = time.perf_counter()
            try:
                data = self.fetcher(
                    self.config,
                    now=local_now,
                    cache_path=self.cache_path,
                    source_cache_dir=self.source_cache_path,
                    calendar_ids=due_calendar_ids,
                    weather_due=weather_due,
                    offline=self.offline,
                )
                self._publish(data)
            except Exception as exc:
                logger.error("Dashboard source refresh failed (%s); cached state is retained.", type(exc).__name__)
            fetch_duration = time.perf_counter() - fetch_started

        before_health = self.data
        updated = self._apply_health(self.data, local_now)
        health_changed = health_changed or updated.status != before_health.status
        if did_fetch or health_changed:
            save_state(updated, self.cache_path)
            self._publish(updated)
            render_started = time.perf_counter()
            try:
                self._render()
            except (OSError, RenderError) as exc:
                logger.error("Dashboard render failed (%s); serving the previous page.", type(exc).__name__)
            render_duration = time.perf_counter() - render_started

        self._fetch_duration += fetch_duration
        self._render_duration += render_duration
        if self._last_metrics is None:
            self._last_metrics = local_now
        elif local_now - self._last_metrics >= METRICS_INTERVAL:
            logger.info(
                "Hourly dashboard metrics: fetch=%.3fs render=%.3fs process_memory=%.1fMiB",
                self._fetch_duration,
                self._render_duration,
                _process_memory_mb(),
            )
            self._fetch_duration = 0.0
            self._render_duration = 0.0
            self._last_metrics = local_now
        return self.data

    def run_scheduler(self, stop_event: threading.Event) -> None:
        """Run scheduler ticks until graceful shutdown is requested."""
        while not stop_event.is_set():
            now = datetime.now().astimezone()
            self.tick(now=now)
            wait_seconds = self._seconds_until_next(now)
            stop_event.wait(wait_seconds)

    def health_payload(self) -> dict[str, object]:
        """Return a URL- and secret-free health view for /healthz."""
        data = self.data
        return {
            "offline": data.status.offline,
            "generated_at": data.generated_at.isoformat(),
            "sources": [
                {
                    "id": self._safe_health_label(source.id),
                    "name": self._safe_health_label(source.name),
                    "kind": source.kind,
                    "state": source.state,
                    "last_update": (
                        source.last_success.isoformat() if source.last_success is not None else None
                    ),
                }
                for source in data.status.sources
            ],
        }

    def _check_connectivity(self) -> None:
        try:
            connected = self.connectivity()
        except Exception as exc:
            logger.warning("Connectivity check failed (%s).", type(exc).__name__)
            connected = False
        if connected:
            self._connectivity_failures = 0
            self.offline = False
        else:
            self._connectivity_failures += 1
            self.offline = self._connectivity_failures >= 2

    def _safe_health_label(self, value: str) -> str:
        """Redact configured feed URLs and URL-shaped labels from health output."""
        for calendar in self.config.calendars:
            if calendar.url is not None:
                secret_url = calendar.url.get_secret_value()
                if secret_url:
                    value = value.replace(secret_url, "[redacted]")
        return re.sub(r"(?i)\b(?:https?|webcal)://\S+", "[redacted]", value)

    def _is_due(self, source_id: str, now: datetime) -> bool:
        due = self._next_due.get(source_id)
        return due is None or now >= due

    def _schedule_next(self, source_id: str, now: datetime, refresh_minutes: int) -> None:
        """Set a source's next due time with bounded ten-percent jitter."""
        factor = 1 + self.random.uniform(-JITTER_FRACTION, JITTER_FRACTION)
        self._next_due[source_id] = now + timedelta(minutes=refresh_minutes * factor)

    def _apply_health(self, data: DashboardData, now: datetime) -> DashboardData:
        intervals = {
            calendar.url_env_var.lower(): timedelta(minutes=calendar.refresh_minutes)
            for calendar in self.config.calendars
            if calendar.enabled
        }
        intervals["weather"] = timedelta(minutes=self.config.weather.refresh_minutes)
        statuses: list[SourceStatus] = []
        for source in data.status.sources:
            interval = intervals.get(source.id)
            if (
                interval is not None
                and source.last_success is not None
                and now - source.last_success >= interval * 2
                and source.state != "stale"
            ):
                source = source.model_copy(
                    update={"state": "stale", "message": "Using last-known-good data."}
                )
            statuses.append(source)
        return data.model_copy(
            update={
                "status": DashboardStatus(offline=self.offline, sources=statuses),
                "generated_at": now,
            }
        )

    def _publish(self, data: DashboardData) -> None:
        with self._data_lock:
            self._data = data

    def _render(self) -> None:
        background_path = Path(self.config.backgrounds.folder)
        if not background_path.is_absolute():
            background_path = PROJECT_ROOT / background_path
        self.renderer(
            self.cache_path,
            self.output_path,
            backgrounds_path=background_path,
            backgrounds_enabled=self.config.backgrounds.enabled,
            change_minutes=self.config.backgrounds.change_minutes,
            clock_show_seconds=self.config.display.clock_show_seconds,
        )

    def _seconds_until_next(self, now: datetime) -> float:
        due_times = [*self._next_due.values()]
        if self._next_connectivity is not None:
            due_times.append(self._next_connectivity)
        if not due_times:
            return 1.0
        return max(0.1, min(60.0, (min(due_times) - now).total_seconds()))


def make_http_server(
    host: str,
    port: int,
    output_path: Path | str,
    runtime: DashboardRuntime,
) -> ThreadingHTTPServer:
    """Create the dashboard HTTP server with health and cache-control routes."""
    output_directory = Path(output_path)

    class DashboardRequestHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(output_directory), **kwargs)

        def do_GET(self) -> None:
            if urlsplit(self.path).path == "/healthz":
                body = json.dumps(runtime.health_payload(), separators=(",", ":")).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            super().do_GET()

        def end_headers(self) -> None:
            path = urlsplit(self.path).path
            if path in {"/data.json", "/healthz"}:
                self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            elif path.startswith(("/static/", "/backgrounds/")):
                self.send_header("Cache-Control", "public, max-age=31536000, immutable")
            else:
                self.send_header("Cache-Control", "no-cache")
            super().end_headers()

        def log_message(self, _format_string: str, *args: object) -> None:
            logger.info("HTTP response: %s", args[1] if len(args) > 1 else "completed")

    return ThreadingHTTPServer((host, port), DashboardRequestHandler)


def run_dashboard(config: DashboardConfig) -> None:
    """Start the cached display, scheduler, and local HTTP server until shutdown."""
    runtime = DashboardRuntime(config)
    now = datetime.now().astimezone()
    runtime.initialize(now=now)
    server = make_http_server(
        config.server.host,
        config.server.port,
        runtime.output_path,
        runtime,
    )
    stop_event = threading.Event()
    server_thread = threading.Thread(
        target=server.serve_forever,
        name="dashboard-http",
        daemon=True,
    )
    scheduler_thread = threading.Thread(
        target=runtime.run_scheduler,
        args=(stop_event,),
        name="dashboard-scheduler",
        daemon=True,
    )
    previous_sigterm = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, lambda _signum, _frame: stop_event.set())
    server_thread.start()
    scheduler_thread.start()
    logger.info("Dashboard server listening on %s:%d.", config.server.host, config.server.port)
    try:
        while not stop_event.wait(0.5):
            pass
    except KeyboardInterrupt:
        logger.info("Shutdown requested.")
    finally:
        stop_event.set()
        scheduler_thread.join()
        server.shutdown()
        server.server_close()
        server_thread.join()
        signal.signal(signal.SIGTERM, previous_sigterm)


def _process_memory_mb() -> float:
    """Return resident process memory in MiB using platform-provided APIs."""
    if platform.system() == "Windows":
        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        get_memory_info = psapi.GetProcessMemoryInfo
        get_memory_info.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ProcessMemoryCounters),
            ctypes.c_ulong,
        ]
        get_memory_info.restype = ctypes.c_int
        process = kernel32.GetCurrentProcess()
        if get_memory_info(
            process,
            ctypes.byref(counters),
            counters.cb,
        ):
            return counters.WorkingSetSize / (1024 * 1024)
        return 0.0
    try:
        with Path("/proc/self/statm").open(encoding="ascii") as statm:
            resident_pages = int(statm.read().split()[1])
        return resident_pages * os.sysconf("SC_PAGE_SIZE") / (1024 * 1024)
    except (OSError, IndexError, ValueError):
        return 0.0
