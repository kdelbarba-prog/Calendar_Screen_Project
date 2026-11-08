"""Command-line entry point for the dashboard."""

from __future__ import annotations

import argparse
import logging

from dashboard.config import ConfigError, load_config
from dashboard.logging_setup import setup_logging

logger = logging.getLogger(__name__)


def main() -> int:
    """Run the requested dashboard command."""
    parser = argparse.ArgumentParser(description="Calendar screen dashboard")
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="validate the configured dashboard and color palette",
    )
    args = parser.parse_args()
    setup_logging()

    if not args.check_config:
        parser.print_help()
        return 0

    try:
        load_config()
    except ConfigError as exc:
        logger.error("Configuration check failed: %s", exc)
        return 1

    logger.info("Configuration is valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
