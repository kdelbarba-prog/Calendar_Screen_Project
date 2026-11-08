"""Shared pytest behavior for offline-by-default test runs."""

from __future__ import annotations

import pytest


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip live tests unless pytest was explicitly invoked with ``-m live``."""
    if config.getoption("-m") == "live":
        return
    skip_live = pytest.mark.skip(reason="live tests require an explicit `pytest -m live` run")
    for item in items:
        if item.get_closest_marker("live") is not None:
            item.add_marker(skip_live)
