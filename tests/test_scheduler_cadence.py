# -*- coding: utf-8 -*-
"""The next wake stays on MIN/MAX_INTERVAL_HOURS after a channel .env reload."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from distribution.publishers.base import Account
from distribution.workers.runtime import (
    load_channel_wake,
    run_scheduler,
    save_channel_wake,
    wake_pin_allowance,
)
from production.formats.pins.config import _load_dotenv_file

_NOW = datetime(2026, 10, 21, 15, 0, tzinfo=timezone.utc)
_CHANNEL = "ancient_knowledge"


def _example_env(path: Path) -> Path:
    """The file a channel reload actually applies. 3–6h is the example default."""
    path.write_text(
        "\n".join(
            [
                "MIN_INTERVAL_HOURS=3",
                "MAX_INTERVAL_HOURS=6",
                "POSTING_INTERVAL_HOURS=3",
                "PINTEREST_PINS_PER_DAY=4",
                "PINTEREST_BOARD_NAME=from-file",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def _isolated_wake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "distribution.workers.runtime.scheduler_state_path",
        lambda: tmp_path / "scheduler" / "next_wake.json",
    )
    monkeypatch.setattr(
        "distribution.workers.runtime._scheduler_channel_ids",
        lambda: [_CHANNEL],
    )
    monkeypatch.setattr("distribution.workers.runtime._utc_now", lambda: _NOW)


def _reload_during_cycle(env_file: Path):
    def cycle(*, dry_run: bool = False) -> list:
        del dry_run
        _load_dotenv_file(env_file)
        return []

    return cycle


def _stop_on_second_sleep():
    sleeps: list[float] = []

    def fake_sleep(seconds: float, stop: dict) -> None:
        sleeps.append(seconds)
        if len(sleeps) > 1:
            stop["flag"] = True

    return sleeps, fake_sleep


def test_dotenv_reload_keeps_the_process_cadence_and_daily_cap(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A file with the 3–6h example and POSTING_INTERVAL_HOURS=3 does not win."""
    monkeypatch.setenv("MIN_INTERVAL_HOURS", "7")
    monkeypatch.setenv("MAX_INTERVAL_HOURS", "9")
    monkeypatch.setenv("PINTEREST_PINS_PER_DAY", "3")
    monkeypatch.setenv("PINTEREST_PINS_PER_RUN", "5")
    _load_dotenv_file(_example_env(tmp_path / ".env"))

    assert os.getenv("MIN_INTERVAL_HOURS") == "7"
    assert os.getenv("MAX_INTERVAL_HOURS") == "9"
    assert os.getenv("PINTEREST_PINS_PER_DAY") == "3"
    assert os.getenv("POSTING_INTERVAL_HOURS") == "3"
    assert os.getenv("PINTEREST_BOARD_NAME") == "from-file"

    from distribution.scheduling.scheduler import PinterestScheduler, get_random_interval

    with caplog.at_level(logging.INFO, logger="distribution.scheduling.scheduler"):
        PinterestScheduler(dry_run=True, no_wait=True, pins_per_run=1)
    assert "interval=7.0-9.0h" in caplog.text
    for _ in range(20):
        minutes = get_random_interval()
        assert 7 * 60 <= minutes <= 9 * 60

    account = Account(branding=_CHANNEL, platform="pinterest", account_id=_CHANNEL)
    empty = wake_pin_allowance(account, now=_NOW, read_rows=lambda _account: ([], True))
    assert empty == 3
    today = [
        {"post_id": "a", "published_utc": "2026-10-21T10:00:00Z"},
        {"post_id": "b", "published_utc": "2026-10-21T12:00:00Z"},
        {"post_id": "c", "published_utc": "2026-10-21T14:00:00Z"},
    ]
    full = wake_pin_allowance(account, now=_NOW, read_rows=lambda _account: (today, True))
    assert full == 0
    two = wake_pin_allowance(
        account,
        now=_NOW,
        read_rows=lambda _account: (today[:2], True),
    )
    assert two == 1


def test_skip_dotenv_does_not_reload_a_repo_env_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """configure() must not pull a developer .env back over the test process."""
    from production.formats.pins import config as cfg

    env_file = tmp_path / ".env"
    env_file.write_text("PINTEREST_BOARD_NAME=from-repo-file\n", encoding="utf-8")
    monkeypatch.setattr(cfg, "ENGINE_ROOT", tmp_path)
    monkeypatch.setenv("OMNI_SKIP_DOTENV", "1")
    monkeypatch.delenv("PINTEREST_BOARD_NAME", raising=False)
    _load_dotenv_file(env_file)
    assert "PINTEREST_BOARD_NAME" not in os.environ
    monkeypatch.delenv("OMNI_SKIP_DOTENV", raising=False)
    _load_dotenv_file(env_file)
    assert os.getenv("PINTEREST_BOARD_NAME") == "from-repo-file"


def test_unset_scheduler_keys_still_load_from_the_env_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("MIN_INTERVAL_HOURS", raising=False)
    monkeypatch.delenv("MAX_INTERVAL_HOURS", raising=False)
    monkeypatch.delenv("PINTEREST_PINS_PER_DAY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "MIN_INTERVAL_HOURS=7\nMAX_INTERVAL_HOURS=9\nPINTEREST_PINS_PER_DAY=3\n",
        encoding="utf-8",
    )
    _load_dotenv_file(env_file)
    assert os.getenv("MIN_INTERVAL_HOURS") == "7"
    assert os.getenv("MAX_INTERVAL_HOURS") == "9"
    assert os.getenv("PINTEREST_PINS_PER_DAY") == "3"


def test_missed_wake_with_an_empty_inventory_keeps_the_7_to_9_hour_gap(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    _isolated_wake: None,
) -> None:
    """The gap after a missed wake is the startup cadence, not the reloaded 3–6h."""
    del _isolated_wake
    save_channel_wake(_CHANNEL, _NOW - timedelta(minutes=10))
    monkeypatch.setenv("MIN_INTERVAL_HOURS", "7")
    monkeypatch.setenv("MAX_INTERVAL_HOURS", "9")
    monkeypatch.setattr("distribution.workers.runtime.newest_pin_published", lambda: None)
    sleeps, fake_sleep = _stop_on_second_sleep()
    monkeypatch.setattr("distribution.workers.runtime._sleep_until", fake_sleep)
    monkeypatch.setattr(
        "distribution.workers.runtime.run_cycle",
        _reload_during_cycle(_example_env(tmp_path / ".env")),
    )
    run_scheduler(once=False, dry_run=False)
    assert sleeps[0] == 0
    assert 7 * 3600 <= sleeps[1] <= 9 * 3600
    assert os.getenv("MIN_INTERVAL_HOURS") == "7"
    assert os.getenv("MAX_INTERVAL_HOURS") == "9"
    saved = load_channel_wake(_CHANNEL)
    assert saved is not None
    gap = (saved - _NOW).total_seconds()
    assert 7 * 3600 <= gap <= 9 * 3600


def test_catchup_after_a_covered_wake_keeps_the_7_to_9_hour_gap(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    _isolated_wake: None,
) -> None:
    """A pin already in the ledger waits out 7–9h, and the following gap does too."""
    del _isolated_wake
    wake = _NOW - timedelta(minutes=8)
    save_channel_wake(_CHANNEL, wake)
    monkeypatch.setenv("MIN_INTERVAL_HOURS", "7")
    monkeypatch.setenv("MAX_INTERVAL_HOURS", "9")
    pin_at = wake - timedelta(minutes=5)

    def channel_reader(accounts):
        del accounts
        return pin_at

    # The startup stamp does not cover the wake. The injected reader does.
    monkeypatch.setattr(
        "distribution.workers.runtime.newest_pin_published",
        lambda: _NOW - timedelta(hours=6),
    )
    monkeypatch.setattr("distribution.workers.runtime._channel_ledger_reader", channel_reader)
    sleeps, fake_sleep = _stop_on_second_sleep()
    monkeypatch.setattr("distribution.workers.runtime._sleep_until", fake_sleep)
    monkeypatch.setattr(
        "distribution.workers.runtime.run_cycle",
        _reload_during_cycle(_example_env(tmp_path / ".env")),
    )
    run_scheduler(once=False, dry_run=False)
    # First wait is the remainder of 7–9h after a pin 13 minutes ago.
    assert 7 * 3600 - 13 * 60 <= sleeps[0] <= 9 * 3600 - 13 * 60
    assert 7 * 3600 <= sleeps[1] <= 9 * 3600


def test_empty_ledger_startup_and_the_next_gap_stay_on_7_to_9_hours(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    _isolated_wake: None,
) -> None:
    del _isolated_wake
    monkeypatch.setenv("MIN_INTERVAL_HOURS", "7")
    monkeypatch.setenv("MAX_INTERVAL_HOURS", "9")
    monkeypatch.setattr("distribution.workers.runtime.newest_pin_published", lambda: None)
    sleeps, fake_sleep = _stop_on_second_sleep()
    monkeypatch.setattr("distribution.workers.runtime._sleep_until", fake_sleep)
    monkeypatch.setattr(
        "distribution.workers.runtime.run_cycle",
        _reload_during_cycle(_example_env(tmp_path / ".env")),
    )
    run_scheduler(once=False, dry_run=False)
    assert 7 * 3600 <= sleeps[0] <= 9 * 3600
    assert 7 * 3600 <= sleeps[1] <= 9 * 3600
