#!/usr/bin/env python3
"""Prepare a local dashboard installation on Windows, macOS, or Linux."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def prepare_local_files(project_root: Path) -> None:
    """Create starter configuration and an empty, private environment file if missing."""
    config_directory = project_root / "config"
    config_directory.mkdir(parents=True, exist_ok=True)

    example_config = config_directory / "config.example.yaml"
    local_config = config_directory / "config.yaml"
    if not local_config.exists():
        shutil.copyfile(example_config, local_config)

    env_file = project_root / ".env"
    try:
        descriptor = os.open(env_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        pass
    else:
        os.close(descriptor)


def virtualenv_python(environment: Path) -> Path:
    """Return the platform-specific Python executable inside a virtual environment."""
    executable = "python.exe" if os.name == "nt" else "python"
    scripts_directory = "Scripts" if os.name == "nt" else "bin"
    return environment / scripts_directory / executable


def install(project_root: Path) -> Path:
    """Create or reuse the local environment, install dependencies, and validate config."""
    environment = project_root / ".venv"
    if not virtualenv_python(environment).exists():
        venv.create(environment, with_pip=True)

    python = virtualenv_python(environment)
    subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "-r",
            str(project_root / "requirements.txt"),
            "-e",
            str(project_root),
        ],
        cwd=project_root,
        check=True,
    )
    subprocess.run(
        [str(python), "-m", "dashboard", "--check-config"],
        cwd=project_root,
        check=True,
    )
    return python


def main() -> int:
    """Set up a local dashboard installation."""
    parser = argparse.ArgumentParser(
        description="Create a virtual environment and install the dashboard."
    )
    parser.parse_args()

    if sys.version_info < (3, 11):
        parser.error("Python 3.11 or newer is required.")

    prepare_local_files(PROJECT_ROOT)
    python = install(PROJECT_ROOT)
    print("Dashboard installed and configuration validated.")
    print(f"Start it with: {python} -m dashboard run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
