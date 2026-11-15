"""Tests for the cross-platform installer setup behavior."""

from pathlib import Path
import os
import runpy


INSTALLER_PATH = Path(__file__).resolve().parents[1] / "deploy" / "install.py"
prepare_local_files = runpy.run_path(str(INSTALLER_PATH))["prepare_local_files"]


def test_prepare_local_files_creates_starters_without_overwriting_existing_files(
    tmp_path: Path,
) -> None:
    config_directory = tmp_path / "config"
    config_directory.mkdir()
    example_config = config_directory / "config.example.yaml"
    example_config.write_text("default: true\n", encoding="utf-8")

    prepare_local_files(tmp_path)

    assert (config_directory / "config.yaml").read_text(encoding="utf-8") == "default: true\n"
    env_file = tmp_path / ".env"
    assert env_file.read_text(encoding="utf-8") == ""
    if os.name != "nt":
        assert env_file.stat().st_mode & 0o077 == 0

    (config_directory / "config.yaml").write_text("local: true\n", encoding="utf-8")
    env_file.write_text("PRIVATE_FEED=value\n", encoding="utf-8")

    prepare_local_files(tmp_path)

    assert (config_directory / "config.yaml").read_text(encoding="utf-8") == "local: true\n"
    assert env_file.read_text(encoding="utf-8") == "PRIVATE_FEED=value\n"
