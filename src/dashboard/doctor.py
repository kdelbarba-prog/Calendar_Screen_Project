"""Local deployment diagnostics that never disclose configured feed URLs."""

from __future__ import annotations

import ipaddress
import logging
import re
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import requests
from pydantic import SecretStr

from dashboard.config import DashboardConfig, PROJECT_ROOT

logger = logging.getLogger(__name__)
MINIMUM_FREE_BYTES = 100 * 1024 * 1024
WEATHER_HOSTS = {
    "nws": "api.weather.gov",
    "openmeteo": "api.open-meteo.com",
}


def run_doctor(
    config: DashboardConfig,
    *,
    output_path: Path | str = PROJECT_ROOT / "output",
    dns_lookup: Callable[..., object] = socket.getaddrinfo,
    https_probe: Callable[..., requests.Response] = requests.get,
    command_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    disk_usage: Callable[[Path], object] = shutil.disk_usage,
) -> int:
    """Check configuration, source connectivity, system time, storage, and rendered output."""
    failed = False

    def report(ok: bool, label: str, detail: str) -> None:
        nonlocal failed
        label = _redact_urls(label)
        detail = _redact_urls(detail)
        message = "%s %s: %s", "PASS" if ok else "FAIL", label, detail
        (logger.info if ok else logger.error)(*message)
        failed = failed or not ok

    try:
        ZoneInfo(config.display.timezone)
        report(True, "timezone", "configured timezone is available")
    except (ValueError, KeyError) as exc:
        report(False, "timezone", f"configured timezone is unavailable ({type(exc).__name__})")

    hosts: list[tuple[str, str, str, int]] = []
    for calendar in config.calendars:
        if not calendar.enabled:
            continue
        value = calendar.url
        url = value.get_secret_value() if isinstance(value, SecretStr) else value
        if not url:
            report(False, f"source {calendar.name}", "feed URL is not configured")
            continue
        if url.lower().startswith("webcal://"):
            url = "https://" + url[len("webcal://") :]
        elif url.lower().startswith("webcal:"):
            url = "https:" + url[len("webcal:") :]
        try:
            parsed = urlsplit(url)
            host = parsed.hostname
            port = parsed.port
        except ValueError:
            host = None
            parsed = None
        if parsed is None or parsed.scheme not in {"https", "http"} or not host:
            report(False, f"source {calendar.name}", "feed address is invalid")
            continue
        scheme = parsed.scheme
        port = port or (443 if scheme == "https" else 80)
        if scheme == "http" and not _is_loopback_host(host):
            report(False, f"source {calendar.name}", "plain HTTP is permitted only for localhost")
            continue
        hosts.append((calendar.name, host, scheme, port))

    providers = [config.weather.provider]
    if config.weather.fallback_provider is not None:
        providers.append(config.weather.fallback_provider)
    for provider in dict.fromkeys(providers):
        hosts.append((f"weather {provider}", WEATHER_HOSTS[provider], "https", 443))

    for source_name, host, scheme, port in hosts:
        try:
            dns_lookup(host, port, type=socket.SOCK_STREAM)
        except (OSError, socket.gaierror) as exc:
            report(False, f"{source_name} DNS", f"host lookup failed ({type(exc).__name__})")
            continue
        report(True, f"{source_name} DNS", "host lookup succeeded")

        authority_host = f"[{host}]" if ":" in host else host
        default_port = 443 if scheme == "https" else 80
        authority_port = f":{port}" if port != default_port else ""
        origin = f"{scheme}://{authority_host}{authority_port}"
        try:
            response = https_probe(origin, timeout=5, stream=True, allow_redirects=False)
            status_code = response.status_code
            response.close()
            reachable = status_code < 500
            detail = (
                "secure endpoint responded"
                if scheme == "https"
                else "localhost HTTP endpoint responded"
            )
            if not reachable:
                detail = f"endpoint returned HTTP {status_code}"
            report(reachable, f"{source_name} endpoint", detail)
        except (requests.RequestException, OSError, ValueError) as exc:
            report(
                False,
                f"{source_name} endpoint",
                f"connection failed ({type(exc).__name__})",
            )

    try:
        result = command_runner(
            ["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        synchronized = result.returncode == 0 and result.stdout.strip().casefold() == "yes"
        report(
            synchronized,
            "NTP",
            "system clock is synchronized" if synchronized else "system clock is not synchronized",
        )
    except (OSError, subprocess.SubprocessError) as exc:
        report(False, "NTP", f"timedatectl check failed ({type(exc).__name__})")

    try:
        free_bytes = disk_usage(PROJECT_ROOT).free
        free_mib = free_bytes / (1024 * 1024)
        report(
            free_bytes >= MINIMUM_FREE_BYTES,
            "disk space",
            f"{free_mib:.0f} MiB free"
            if free_bytes >= MINIMUM_FREE_BYTES
            else f"only {free_mib:.0f} MiB free; need at least 100 MiB",
        )
    except OSError as exc:
        report(False, "disk space", f"usage check failed ({type(exc).__name__})")

    page_exists = (Path(output_path) / "index.html").is_file()
    report(page_exists, "rendered page", "output/index.html exists" if page_exists else "output/index.html is missing")
    return int(failed)


def _is_loopback_host(hostname: str) -> bool:
    """Recognize localhost names and loopback IP addresses."""
    host = hostname.rstrip(".").casefold()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _redact_urls(value: str) -> str:
    """Keep accidental URL-shaped names or diagnostics out of console logs."""
    return re.sub(r"(?i)\b(?:https?|webcal)://\S+", "[redacted]", value)
