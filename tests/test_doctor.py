"""Tests for deployment diagnostics without real network or system dependencies."""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from pydantic import SecretStr

from dashboard.config import CalendarConfig, load_config
from dashboard.doctor import run_doctor

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _config():
    config = load_config(config_path=PROJECT_ROOT / "config" / "config.example.yaml")
    calendar = CalendarConfig(
        name="Private calendar",
        url_env_var="PRIVATE_URL",
        color="blue",
        url=SecretStr("https://private.example/secret/path.ics?token=hidden"),
    )
    return config.model_copy(update={"calendars": [calendar]})


def test_doctor_checks_sources_without_logging_urls(
    tmp_path: Path,
    caplog,
) -> None:
    config = _config()
    output = tmp_path / "output"
    output.mkdir()
    (output / "index.html").write_text("<html></html>", encoding="utf-8")
    looked_up: list[tuple[str, int]] = []
    probed: list[str] = []

    def dns_lookup(host: str, port: int, **_kwargs) -> list[object]:
        looked_up.append((host, port))
        return []

    class Response:
        status_code = 404

        def close(self) -> None:
            pass

    def https_probe(url: str, **_kwargs) -> Response:
        probed.append(url)
        return Response()

    def command_runner(*_args, **_kwargs):
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="yes\n", stderr="")

    caplog.set_level(logging.INFO, logger="dashboard.doctor")
    result = run_doctor(
        config,
        output_path=output,
        dns_lookup=dns_lookup,
        https_probe=https_probe,
        command_runner=command_runner,
        disk_usage=lambda _path: SimpleNamespace(free=2_000_000_000),
    )

    logs = caplog.text
    assert result == 0
    assert ("private.example", 443) in looked_up
    assert "https://private.example" in probed
    assert "private.example" not in logs
    assert "secret/path" not in logs
    assert "token=hidden" not in logs
    assert "PASS Private calendar DNS" in logs
    assert "PASS NTP" in logs


def test_doctor_returns_failure_for_source_ntp_disk_and_missing_page(
    tmp_path: Path,
) -> None:
    config = _config()

    def dns_lookup(_host: str, _port: int, **_kwargs) -> list[object]:
        return []

    class Response:
        status_code = 503

        def close(self) -> None:
            pass

    result = run_doctor(
        config,
        output_path=tmp_path / "missing-output",
        dns_lookup=dns_lookup,
        https_probe=lambda *_args, **_kwargs: Response(),
        command_runner=lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="no\n",
            stderr="",
        ),
        disk_usage=lambda _path: SimpleNamespace(free=1),
    )

    assert result == 1


def test_doctor_rejects_non_local_plain_http_feed_without_network(
    tmp_path: Path,
    caplog,
) -> None:
    config = _config()
    calendar = config.calendars[0].model_copy(
        update={"url": SecretStr("http://calendar.example/feed.ics")}
    )
    config = config.model_copy(update={"calendars": [calendar]})
    dns_calls: list[str] = []

    class Response:
        status_code = 200

        def close(self) -> None:
            pass

    result = run_doctor(
        config,
        output_path=tmp_path,
        dns_lookup=lambda host, _port, **_kwargs: dns_calls.append(host),
        https_probe=lambda *_args, **_kwargs: Response(),
        command_runner=lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="yes",
            stderr="",
        ),
        disk_usage=lambda _path: SimpleNamespace(free=2_000_000_000),
    )

    assert result == 1
    assert "calendar.example" not in dns_calls


def test_doctor_command_loads_config_and_returns_diagnostic_status(
    monkeypatch,
) -> None:
    from dashboard.__main__ import main

    config = _config()
    monkeypatch.setattr(sys, "argv", ["dashboard", "doctor"])
    monkeypatch.setattr("dashboard.__main__.load_config", lambda: config)
    monkeypatch.setattr("dashboard.__main__.run_doctor", lambda value: int(value is config))

    assert main() == 1
