# -*- coding: utf-8 -*-
"""One CLI takes the channel. The old *_main entry points are gone."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["OUTPUT_PATH"] = str(_REPO / "outputs")
    env["OMNI_SKIP_DOTENV"] = "1"
    return subprocess.run(
        [sys.executable, *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=_REPO,
        check=False,
    )


def test_channel_cli_help_names_the_channel() -> None:
    proc = _run(["tools/channel_cli.py", "--help"])
    assert proc.returncode == 0, proc.stderr
    assert "--channel" in proc.stdout


def test_entry_point_shims_are_gone() -> None:
    assert not (_REPO / "agents" / "posting" / "pinterest_main.py").exists()
    assert not (_REPO / "channels_config" / "endless_summer_paradise" / "esp_main.py").exists()
    assert not (
        _REPO
        / "channels_config"
        / "principles_of_wealth_finance_economics"
        / "wealth_main.py"
    ).exists()


def test_pinterest_cli_accepts_schedule_help() -> None:
    proc = _run(["tools/pinterest_cli.py", "schedule", "--help"])
    assert proc.returncode == 0, proc.stderr
    assert "--channel" in proc.stdout


def test_channel_cli_forwards_pinterest_help() -> None:
    proc = _run(["tools/channel_cli.py", "--channel", "anna_protocol", "schedule", "--help"])
    assert proc.returncode == 0, proc.stderr
    assert "--channel" in proc.stdout


def test_channel_cli_runs_the_folder_cli() -> None:
    wealth = _run(
        [
            "tools/channel_cli.py",
            "--channel",
            "principles_of_wealth_finance_economics",
            "--help",
        ]
    )
    assert wealth.returncode == 0, wealth.stderr
    assert "scan" in wealth.stdout
    assert "publish" in wealth.stdout

    esp = _run(
        [
            "tools/channel_cli.py",
            "--channel",
            "endless_summer_paradise",
            "scan",
            "--help",
        ]
    )
    assert esp.returncode == 0, esp.stderr
    assert "--source-dir" in esp.stdout

    direct = _run(
        [
            "tools/channel_cli.py",
            "--channel",
            "endless_summer_paradise",
            "reschedule",
            "--help",
        ]
    )
    assert direct.returncode == 0, direct.stderr
    assert "--interval" in direct.stdout
