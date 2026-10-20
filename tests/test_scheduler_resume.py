# -*- coding: utf-8 -*-
"""The scheduler resumes from the newest PinLedger published_utc."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agents.media.b2_budget import B2DailyCapError, reset_caches
from agents.media.b2_client import read_object_outcome, read_object_text
from agents.posting.pinterest_engine.ledger import PinLedger
from omni.models import Account
from omni.worker import (
    newest_pin_published,
    run_scheduler,
    startup_delay_seconds,
)

_NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


class _Body:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


class _Missing(Exception):
    def __init__(self) -> None:
        super().__init__("missing")
        self.response = {"Error": {"Code": "NoSuchKey"}}


class _Client:
    def __init__(self, objects: dict[str, bytes] | None = None, error: Exception | None = None) -> None:
        self.objects = objects or {}
        self.error = error
        self.gets = 0
        self.puts = 0

    def get_object(self, **kwargs):
        self.gets += 1
        if self.error is not None:
            raise self.error
        key = kwargs["Key"]
        if key not in self.objects:
            raise _Missing()
        return {"Body": _Body(self.objects[key])}

    def put_object(self, **kwargs):
        self.puts += 1
        return {}


def _account(account_id: str) -> Account:
    return Account(branding=account_id, platform="pinterest", account_id=account_id)


@pytest.fixture
def budget(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("B2_USAGE_PATH", str(tmp_path / "usage.json"))
    monkeypatch.delenv("B2_DAILY_CLASS_B", raising=False)
    reset_caches()
    return tmp_path


def test_missing_ledger_object_is_empty_and_reachable(budget: Path) -> None:
    del budget
    client = _Client()
    text, status = read_object_outcome(
        "pinterest/ledger/acct.jsonl", client=client, bucket="bucket"
    )
    assert text is None
    assert status == "missing"


def test_transport_failure_is_unreachable(budget: Path) -> None:
    del budget

    class _Down(Exception):
        pass

    client = _Client(error=_Down("timeout"))
    text, status = read_object_outcome(
        "pinterest/ledger/acct.jsonl", client=client, bucket="bucket"
    )
    assert text is None
    assert status == "unreachable"


def test_daily_cap_is_unreachable_without_changing_read_object_text(
    budget: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del budget
    monkeypatch.setenv("B2_DAILY_CLASS_B", "0")
    reset_caches()
    client = _Client({"pinterest/ledger/acct.jsonl": b'{"published_utc":"2026-10-20T01:00:00Z"}\n'})
    text, status = read_object_outcome(
        "pinterest/ledger/acct.jsonl", client=client, bucket="bucket"
    )
    assert text is None
    assert status == "unreachable"
    assert client.gets == 0
    with pytest.raises(B2DailyCapError):
        read_object_text("pinterest/ledger/acct.jsonl", client=client, bucket="bucket")


def test_read_rows_uses_remote_rows_and_does_not_write(budget: Path, tmp_path: Path) -> None:
    del budget
    raw = b'{"published_utc":"2026-10-20T02:00:00Z"}\n'
    client = _Client({"pinterest/ledger/acct.jsonl": raw})
    cache = tmp_path / "acct.jsonl"
    ledger = PinLedger("acct", client=client, bucket="bucket", cache_path=cache)
    rows, reachable = ledger.read_rows()
    assert reachable is True
    assert rows == [{"published_utc": "2026-10-20T02:00:00Z"}]
    assert client.puts == 0
    assert not cache.exists()


def test_local_cache_covers_an_unreachable_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "agents.media.b2_client.read_object_outcome",
        lambda *args, **kwargs: (None, "unreachable"),
    )
    cache = tmp_path / "acct.jsonl"
    cache.write_text('{"published_utc":"2026-10-20T01:00:00Z"}\n', encoding="utf-8")
    before = cache.read_bytes()
    ledger = PinLedger("acct", cache_path=cache)
    rows, reachable = ledger.read_rows()
    assert reachable is True
    assert rows[0]["published_utc"] == "2026-10-20T01:00:00Z"
    assert cache.read_bytes() == before


def test_unreachable_without_a_cache_is_not_readable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "agents.media.b2_client.read_object_outcome",
        lambda *args, **kwargs: (None, "unreachable"),
    )
    ledger = PinLedger("acct", cache_path=tmp_path / "missing.jsonl")
    rows, reachable = ledger.read_rows()
    assert rows == []
    assert reachable is False


def test_newest_published_utc_wins_and_sets_the_remaining_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIN_INTERVAL_HOURS", "4")
    monkeypatch.setenv("MAX_INTERVAL_HOURS", "4")

    def read_rows(account: Account) -> tuple[list[dict], bool]:
        if account.account_id == "older":
            return [{"published_utc": "2026-10-20T08:00:00Z"}], True
        return [
            {"published_utc": "not-a-time"},
            {"published_utc": "2026-10-20T10:00:00"},
            {"published_utc": "2026-10-20T09:00:00Z", "pin_recorded": False},
        ], True

    stamp = newest_pin_published(
        [_account("older"), _account("newer")],
        read_rows=read_rows,
    )
    assert stamp == datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)
    assert startup_delay_seconds(stamp, now=_NOW) == pytest.approx(2 * 3600)


def test_elapsed_cadence_does_not_add_another_startup_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIN_INTERVAL_HOURS", "4")
    monkeypatch.setenv("MAX_INTERVAL_HOURS", "4")
    last = _NOW - timedelta(hours=10)
    assert startup_delay_seconds(last, now=_NOW) == 0


def test_empty_or_unreachable_ledger_keeps_the_random_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIN_INTERVAL_HOURS", "3")
    monkeypatch.setenv("MAX_INTERVAL_HOURS", "6")
    assert startup_delay_seconds(None, now=_NOW, rng=__import__("random").Random(7)) >= 3 * 3600

    def read_rows(account: Account) -> tuple[list[dict], bool]:
        if account.account_id == "recent":
            return [{"published_utc": "2026-10-20T11:30:00Z"}], True
        return [], False

    assert (
        newest_pin_published(
            [_account("recent"), _account("down")],
            read_rows=read_rows,
        )
        is None
    )
    assert newest_pin_published([_account("empty")], read_rows=lambda _account: ([], True)) is None


def test_scheduler_sleeps_the_remainder_then_a_full_cadence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    last = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("omni.worker.newest_pin_published", lambda: last)
    monkeypatch.setattr("omni.worker._utc_now", lambda: _NOW)
    monkeypatch.setattr("omni.worker.interval_seconds", lambda rng=None: 4 * 3600)
    calls: list[float] = []

    def fake_sleep(seconds: float, stop: dict) -> None:
        calls.append(seconds)
        if len(calls) > 1:
            stop["flag"] = True

    monkeypatch.setattr("omni.worker._sleep_until", fake_sleep)
    monkeypatch.setattr("omni.worker.run_cycle", lambda *, dry_run=False: [])
    run_scheduler(once=False, dry_run=False)
    assert calls[0] == pytest.approx(2 * 3600)
    assert calls[1] == pytest.approx(4 * 3600)


def test_scheduler_skips_the_startup_delay_when_the_cadence_has_elapsed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    last = _NOW - timedelta(hours=10)
    monkeypatch.setattr("omni.worker.newest_pin_published", lambda: last)
    monkeypatch.setattr("omni.worker._utc_now", lambda: _NOW)
    monkeypatch.setattr("omni.worker.interval_seconds", lambda rng=None: 4 * 3600)
    calls: list[float] = []

    def fake_sleep(seconds: float, stop: dict) -> None:
        calls.append(seconds)
        if len(calls) > 1:
            stop["flag"] = True

    monkeypatch.setattr("omni.worker._sleep_until", fake_sleep)
    monkeypatch.setattr("omni.worker.run_cycle", lambda *, dry_run=False: [])
    run_scheduler(once=False, dry_run=False)
    assert calls[0] == 0
    assert calls[1] == pytest.approx(4 * 3600)


def test_once_and_dry_run_do_not_read_the_pin_ledger(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args, **_kwargs):
        raise AssertionError("the startup delay must not run")

    cycles: list[bool] = []
    monkeypatch.setattr("omni.worker.newest_pin_published", boom)
    monkeypatch.setattr("omni.worker.interval_seconds", boom)
    monkeypatch.setattr(
        "omni.worker.run_cycle",
        lambda *, dry_run=False: cycles.append(dry_run) or [],
    )
    run_scheduler(once=True, dry_run=False)
    run_scheduler(once=True, dry_run=True)
    assert cycles == [False, True]

    reads: list[str] = []
    monkeypatch.setattr(
        "omni.worker.newest_pin_published",
        lambda: reads.append("ledger"),
    )
    monkeypatch.setattr("omni.worker.interval_seconds", lambda rng=None: 5)
    monkeypatch.setattr(
        "omni.worker._sleep_until",
        lambda seconds, stop: stop.__setitem__("flag", True),
    )
    run_scheduler(once=False, dry_run=True)
    assert reads == []
    assert cycles == [False, True, True]
