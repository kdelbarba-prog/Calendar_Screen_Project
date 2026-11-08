"""Formatting helpers for calendar event locations."""

from __future__ import annotations

import re
from typing import Literal
from urllib.parse import urlparse

LocationMode = Literal["city", "full", "none"]

_STATE_ABBREVIATIONS = frozenset(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN "
    "MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA "
    "WA WV WI WY DC".split()
)
_VIDEO_HOSTS = (
    "zoom.us",
    "meet.google.com",
    "teams.microsoft.com",
    "teams.live.com",
    "webex.com",
    "gotomeeting.com",
    "goto.com",
    "skype.com",
    "facetime.apple.com",
)
_SUITE_PATTERN = re.compile(
    r"^(?:suite|ste\.?|unit|apt\.?|apartment|floor|fl\.?|#)\s*[\w-]+$",
    re.IGNORECASE,
)


def _is_video_call_url(value: str) -> bool:
    """Return whether a location is a known video-call URL."""
    candidate = value if "://" in value else f"https://{value}"
    host = urlparse(candidate).hostname
    if host is None:
        return False
    hostname = host.lower().removeprefix("www.")
    return any(hostname == domain or hostname.endswith(f".{domain}") for domain in _VIDEO_HOSTS)


def _extract_city(value: str) -> str:
    """Extract a city before a US state abbreviation when the address is recognizable."""
    parts = [part.strip() for part in value.split(",") if part.strip()]
    for index, part in enumerate(parts):
        state_match = re.match(r"^([A-Z]{2})(?:\s+\d{5}(?:-\d{4})?)?(?:\s+USA?)?$", part, re.I)
        if state_match and state_match.group(1).upper() in _STATE_ABBREVIATIONS:
            preceding = [
                candidate
                for candidate in parts[:index]
                if not _SUITE_PATTERN.match(candidate)
            ]
            if preceding:
                city = preceding[-1]
                if city and not re.fullmatch(r"\d+.*", city):
                    return city
            return value

        combined_match = re.match(
            r"^(.+?),\s*([A-Z]{2})\s+\d{5}(?:-\d{4})?(?:\s+USA?)?$",
            part,
            re.I,
        )
        if (
            combined_match
            and combined_match.group(2).upper() in _STATE_ABBREVIATIONS
        ):
            city = combined_match.group(1).strip()
            return city if city else value
    return value


def format_location(raw: str | None, mode: LocationMode) -> str:
    """Format a location for display, hiding video-call links and handling US addresses."""
    if raw is None:
        return ""
    value = raw.strip()
    if not value or mode == "none" or _is_video_call_url(value):
        return ""
    if mode == "full":
        return value
    if mode == "city":
        return _extract_city(value)
    raise ValueError(f"Unsupported location mode: {mode!r}")
