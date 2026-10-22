# -*- coding: utf-8 -*-
"""A restart keeps the channel wake that was already chosen."""

from __future__ import annotations

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
