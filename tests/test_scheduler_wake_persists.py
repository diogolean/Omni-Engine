# -*- coding: utf-8 -*-
"""A restart keeps the channel wake that was already chosen."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from distribution.workers.runtime import (
    load_channel_wake,
    run_scheduler,
    save_channel_wake,
)

_NOW = datetime(2026, 10, 21, 2, 58, tzinfo=timezone.utc)
_CHANNEL = "ancient_knowledge"


@pytest.fixture(autouse=True)
def _isolated_scheduler_wake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    state = tmp_path / "scheduler" / "next_wake.json"
    monkeypatch.setattr("distribution.workers.runtime.scheduler_state_path", lambda: state)
    monkeypatch.setattr(
        "distribution.workers.runtime._scheduler_channel_ids",
        lambda: [_CHANNEL],
    )
    monkeypatch.setattr("distribution.workers.runtime._utc_now", lambda: _NOW)


def test_restart_before_the_wake_reuses_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """A future next_wake_utc is the clock. A new random gap is not drawn."""
    wake = _NOW + timedelta(hours=2)
    save_channel_wake(_CHANNEL, wake)

    def _redraw(rng=None):
        del rng
        raise AssertionError("a restart must not redraw a wake that is still ahead")

    monkeypatch.setattr("distribution.workers.runtime.interval_seconds", _redraw)
    monkeypatch.setattr(
        "distribution.workers.runtime.newest_pin_published",
        lambda: _NOW - timedelta(hours=1),
    )
    sleeps: list[float] = []

    def fake_sleep(seconds: float, stop: dict) -> None:
        sleeps.append(seconds)
        stop["flag"] = True

    def refuse_publish(*, dry_run: bool = False) -> list:
        del dry_run
        raise AssertionError("the wake is still ahead")

    monkeypatch.setattr("distribution.workers.runtime._sleep_until", fake_sleep)
    monkeypatch.setattr("distribution.workers.runtime.run_cycle", refuse_publish)
    run_scheduler(once=False, dry_run=False)
    assert sleeps == [pytest.approx(2 * 3600)]
    assert load_channel_wake(_CHANNEL) == wake


def test_restart_after_a_missed_wake_publishes_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """23:58 missed by a few minutes during deploy still runs, once."""
    missed = _NOW - timedelta(minutes=10)
    save_channel_wake(_CHANNEL, missed)
    monkeypatch.setattr("distribution.workers.runtime.interval_seconds", lambda rng=None: 4 * 3600)
    monkeypatch.setattr(
        "distribution.workers.runtime.newest_pin_published",
        lambda: _NOW - timedelta(hours=6),
    )
    sleeps: list[float] = []
    cycles: list[bool] = []

    def fake_sleep(seconds: float, stop: dict) -> None:
        sleeps.append(seconds)
        if len(sleeps) > 1:
            stop["flag"] = True

    monkeypatch.setattr("distribution.workers.runtime._sleep_until", fake_sleep)
    monkeypatch.setattr(
        "distribution.workers.runtime.run_cycle",
        lambda *, dry_run=False: cycles.append(dry_run) or [],
    )
    run_scheduler(once=False, dry_run=False)
    assert sleeps[0] == 0
    assert cycles == [False]
    assert load_channel_wake(_CHANNEL) == _NOW + timedelta(hours=4)


def test_missed_wake_does_not_post_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    """The missed slot is consumed before publish, so the next start waits."""
    save_channel_wake(_CHANNEL, _NOW - timedelta(minutes=10))
    monkeypatch.setattr("distribution.workers.runtime.interval_seconds", lambda rng=None: 4 * 3600)
    monkeypatch.setattr(
        "distribution.workers.runtime.newest_pin_published",
        lambda: _NOW - timedelta(hours=6),
    )
    cycles: list[bool] = []
    calls = {"n": 0}

    def counting_sleep(seconds: float, stop: dict) -> None:
        calls["n"] += 1
        if calls["n"] > 1:
            stop["flag"] = True

    monkeypatch.setattr("distribution.workers.runtime._sleep_until", counting_sleep)
    monkeypatch.setattr(
        "distribution.workers.runtime.run_cycle",
        lambda *, dry_run=False: cycles.append(dry_run) or [],
    )
    run_scheduler(once=False, dry_run=False)
    assert cycles == [False]

    def wait_only(seconds: float, stop: dict) -> None:
        assert seconds == pytest.approx(4 * 3600)
        stop["flag"] = True

    monkeypatch.setattr("distribution.workers.runtime._sleep_until", wait_only)
    run_scheduler(once=False, dry_run=False)
    assert cycles == [False]


def test_wake_older_than_the_grace_is_redrawn(monkeypatch: pytest.MonkeyPatch) -> None:
    """Past the 30-minute grace, the old slot is not fired late."""
    save_channel_wake(_CHANNEL, _NOW - timedelta(minutes=31))
    monkeypatch.setenv("MIN_INTERVAL_HOURS", "4")
    monkeypatch.setenv("MAX_INTERVAL_HOURS", "4")
    monkeypatch.setattr(
        "distribution.workers.runtime.newest_pin_published",
        lambda: _NOW - timedelta(hours=1),
    )
    sleeps: list[float] = []

    def fake_sleep(seconds: float, stop: dict) -> None:
        sleeps.append(seconds)
        stop["flag"] = True

    def refuse_publish(*, dry_run: bool = False) -> list:
        del dry_run
        raise AssertionError("stale wake fired")

    monkeypatch.setattr("distribution.workers.runtime._sleep_until", fake_sleep)
    monkeypatch.setattr("distribution.workers.runtime.run_cycle", refuse_publish)
    run_scheduler(once=False, dry_run=False)
    assert sleeps == [pytest.approx(3 * 3600)]
    assert load_channel_wake(_CHANNEL) == _NOW + timedelta(hours=3)


def test_restart_during_publish_does_not_repeat_the_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pin within 5 minutes before the saved wake is that slot, already posted."""
    wake = _NOW - timedelta(minutes=8)
    pin_at = wake - timedelta(minutes=5)
    save_channel_wake(_CHANNEL, wake)
    monkeypatch.setenv("MIN_INTERVAL_HOURS", "4")
    monkeypatch.setenv("MAX_INTERVAL_HOURS", "4")
    asked: list[list[str]] = []

    def channel_reader(accounts):
        asked.append([account.account_id for account in accounts])
        return pin_at

    # The startup stamp does not cover the wake. The injected reader does.
    monkeypatch.setattr(
        "distribution.workers.runtime.newest_pin_published",
        lambda: _NOW - timedelta(hours=6),
    )
    monkeypatch.setattr("distribution.workers.runtime._channel_ledger_reader", channel_reader)
    sleeps: list[float] = []

    def fake_sleep(seconds: float, stop: dict) -> None:
        sleeps.append(seconds)
        stop["flag"] = True

    def refuse_publish(*, dry_run: bool = False) -> list:
        del dry_run
        raise AssertionError("the slot is already in the ledger")

    monkeypatch.setattr("distribution.workers.runtime._sleep_until", fake_sleep)
    monkeypatch.setattr("distribution.workers.runtime.run_cycle", refuse_publish)
    run_scheduler(once=False, dry_run=False)
    remaining = 4 * 3600 - 13 * 60
    assert asked == [[_CHANNEL]]
    assert sleeps == [pytest.approx(remaining)]
    assert load_channel_wake(_CHANNEL) == _NOW + timedelta(seconds=remaining)


def test_wake_write_failure_keeps_the_ledger_delay(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    """A read-only wake file logs a warning and the in-memory delay still applies."""
    blocker = tmp_path / "scheduler"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(
        "distribution.workers.runtime.scheduler_state_path",
        lambda: blocker / "next_wake.json",
    )
    monkeypatch.setenv("MIN_INTERVAL_HOURS", "4")
    monkeypatch.setenv("MAX_INTERVAL_HOURS", "4")
    monkeypatch.setattr(
        "distribution.workers.runtime.newest_pin_published",
        lambda: _NOW - timedelta(hours=1),
    )
    sleeps: list[float] = []

    def fake_sleep(seconds: float, stop: dict) -> None:
        sleeps.append(seconds)
        stop["flag"] = True

    def refuse_publish(*, dry_run: bool = False) -> list:
        del dry_run
        raise AssertionError("the startup delay was skipped")

    monkeypatch.setattr("distribution.workers.runtime._sleep_until", fake_sleep)
    monkeypatch.setattr("distribution.workers.runtime.run_cycle", refuse_publish)
    with caplog.at_level(logging.WARNING, logger="omni.worker"):
        save_channel_wake(_CHANNEL, _NOW + timedelta(hours=2))
        run_scheduler(once=False, dry_run=False)
    assert sleeps == [pytest.approx(3 * 3600)]
    assert any("Could not save scheduler wake" in record.message for record in caplog.records)


def test_channel_reader_is_injected_not_inspected() -> None:
    """Per-channel ledger reads take an injected reader, not inspect.signature."""
    source = (
        Path(__file__)
        .resolve()
        .parents[1]
        .joinpath("distribution", "workers", "runtime.py")
        .read_text(encoding="utf-8")
    )
    assert "inspect" not in source
    assert "_channel_ledger_reader" in source
