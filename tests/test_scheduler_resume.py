# -*- coding: utf-8 -*-
"""The scheduler resumes from the newest PinLedger published_utc."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agents.media.b2_budget import B2DailyCapError, reset_caches
from agents.media.b2_client import read_object_outcome, read_object_text
from agents.posting.pinterest_engine.ledger import PinLedger
from distribution.publishers.base import Account
from distribution.workers.runtime import (
    newest_pin_published,
    run_scheduler,
    startup_delay_seconds,
    wake_pin_allowance,
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
    monkeypatch.setattr("distribution.workers.runtime.newest_pin_published", lambda: last)
    monkeypatch.setattr("distribution.workers.runtime._utc_now", lambda: _NOW)
    monkeypatch.setattr("distribution.workers.runtime.interval_seconds", lambda rng=None: 4 * 3600)
    calls: list[float] = []

    def fake_sleep(seconds: float, stop: dict) -> None:
        calls.append(seconds)
        if len(calls) > 1:
            stop["flag"] = True

    monkeypatch.setattr("distribution.workers.runtime._sleep_until", fake_sleep)
    monkeypatch.setattr("distribution.workers.runtime.run_cycle", lambda *, dry_run=False: [])
    run_scheduler(once=False, dry_run=False)
    assert calls[0] == pytest.approx(2 * 3600)
    assert calls[1] == pytest.approx(4 * 3600)


def test_scheduler_skips_the_startup_delay_when_the_cadence_has_elapsed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    last = _NOW - timedelta(hours=10)
    monkeypatch.setattr("distribution.workers.runtime.newest_pin_published", lambda: last)
    monkeypatch.setattr("distribution.workers.runtime._utc_now", lambda: _NOW)
    monkeypatch.setattr("distribution.workers.runtime.interval_seconds", lambda rng=None: 4 * 3600)
    calls: list[float] = []

    def fake_sleep(seconds: float, stop: dict) -> None:
        calls.append(seconds)
        if len(calls) > 1:
            stop["flag"] = True

    monkeypatch.setattr("distribution.workers.runtime._sleep_until", fake_sleep)
    monkeypatch.setattr("distribution.workers.runtime.run_cycle", lambda *, dry_run=False: [])
    run_scheduler(once=False, dry_run=False)
    assert calls[0] == 0
    assert calls[1] == pytest.approx(4 * 3600)


def test_once_and_dry_run_do_not_read_the_pin_ledger(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args, **_kwargs):
        raise AssertionError("the startup delay must not run")

    cycles: list[bool] = []
    monkeypatch.setattr("distribution.workers.runtime.newest_pin_published", boom)
    monkeypatch.setattr("distribution.workers.runtime.interval_seconds", boom)
    monkeypatch.setattr(
        "distribution.workers.runtime.run_cycle",
        lambda *, dry_run=False: cycles.append(dry_run) or [],
    )
    run_scheduler(once=True, dry_run=False)
    run_scheduler(once=True, dry_run=True)
    assert cycles == [False, True]

    reads: list[str] = []
    monkeypatch.setattr(
        "distribution.workers.runtime.newest_pin_published",
        lambda: reads.append("ledger"),
    )
    monkeypatch.setattr("distribution.workers.runtime.interval_seconds", lambda rng=None: 5)
    monkeypatch.setattr(
        "distribution.workers.runtime._sleep_until",
        lambda seconds, stop: stop.__setitem__("flag", True),
    )
    run_scheduler(once=False, dry_run=True)
    assert reads == []
    assert cycles == [False, True, True]


def test_future_published_utc_is_clamped_to_max_interval_hours(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIN_INTERVAL_HOURS", "3")
    monkeypatch.setenv("MAX_INTERVAL_HOURS", "6")
    future = _NOW + timedelta(days=9)
    delay = startup_delay_seconds(future, now=_NOW, rng=__import__("random").Random(1))
    assert delay == pytest.approx(6 * 3600)

    monkeypatch.setenv("MIN_INTERVAL_HOURS", "4")
    monkeypatch.setenv("MAX_INTERVAL_HOURS", "4")
    skewed = _NOW + timedelta(minutes=30)
    assert startup_delay_seconds(skewed, now=_NOW) == pytest.approx(4 * 3600)
    recent = _NOW - timedelta(hours=1)
    assert startup_delay_seconds(recent, now=_NOW) == pytest.approx(3 * 3600)


def test_scheduler_startup_sleep_is_clamped_when_the_pin_is_in_the_future(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIN_INTERVAL_HOURS", "4")
    monkeypatch.setenv("MAX_INTERVAL_HOURS", "4")
    future = _NOW + timedelta(days=3)
    monkeypatch.setattr("distribution.workers.runtime.newest_pin_published", lambda: future)
    monkeypatch.setattr("distribution.workers.runtime._utc_now", lambda: _NOW)
    calls: list[float] = []

    def fake_sleep(seconds: float, stop: dict) -> None:
        calls.append(seconds)
        stop["flag"] = True

    monkeypatch.setattr("distribution.workers.runtime._sleep_until", fake_sleep)
    monkeypatch.setattr("distribution.workers.runtime.run_cycle", lambda *, dry_run=False: [])
    run_scheduler(once=False, dry_run=False)
    assert calls == [pytest.approx(4 * 3600)]


def test_daily_allowance_counts_recorded_pins_from_today_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PINTEREST_PINS_PER_RUN", "5")
    monkeypatch.setenv("PINTEREST_PINS_PER_DAY", "2")
    rows = [
        {"post_id": "yesterday", "published_utc": "2026-10-19T23:00:00Z"},
        # 04:00Z is 01:00 in America/Sao_Paulo, so this pin is on the 20th.
        {"post_id": "today", "published_utc": "2026-10-20T04:00:00Z"},
        {
            "post_id": "note",
            "published_utc": "2026-10-20T05:00:00Z",
            "pin_recorded": False,
        },
    ]
    allowed = wake_pin_allowance(
        _account("acct"),
        now=_NOW,
        read_rows=lambda _account: (rows, True),
    )
    assert allowed == 1

    unread = wake_pin_allowance(
        _account("acct"),
        now=_NOW,
        read_rows=lambda _account: ([], False),
    )
    assert unread == 2


def _drive_immediate_restart(monkeypatch: pytest.MonkeyPatch, account: Account) -> None:
    """Last pin is older than the cadence, so the first sleep is zero."""
    last = _NOW - timedelta(hours=10)
    monkeypatch.setattr("distribution.workers.runtime.newest_pin_published", lambda: last)
    monkeypatch.setattr("distribution.workers.runtime._utc_now", lambda: _NOW)
    monkeypatch.setattr("distribution.workers.runtime.interval_seconds", lambda rng=None: 4 * 3600.0)
    monkeypatch.setattr("distribution.workers.runtime.active_pinterest_accounts", lambda: [account])
    sleeps: list[float] = []

    def fake_sleep(seconds: float, stop: dict) -> None:
        sleeps.append(seconds)
        if len(sleeps) > 1:
            stop["flag"] = True

    monkeypatch.setattr("distribution.workers.runtime._sleep_until", fake_sleep)
    run_scheduler(once=False, dry_run=False)
    assert sleeps[0] == 0


def test_immediate_restart_publishes_nothing_once_the_daily_cap_is_full(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PINTEREST_PINS_PER_RUN", "5")
    monkeypatch.setenv("PINTEREST_PINS_PER_DAY", "2")
    rows = [
        {"post_id": "already-one", "published_utc": "2026-10-20T04:00:00Z"},
        {"post_id": "already-two", "published_utc": "2026-10-20T08:00:00Z"},
        {
            "post_id": "facebook-note",
            "published_utc": "2026-10-20T09:00:00Z",
            "pin_recorded": False,
        },
    ]
    monkeypatch.setattr("distribution.workers.runtime._read_account_ledger", lambda _account: (rows, True))

    def refuse(*_args, **_kwargs):
        raise AssertionError("the daily cap must stop the wake before publish")

    monkeypatch.setattr("distribution.scheduling.drip.publish_account", refuse)
    captured: list[dict] = []
    import distribution.workers.runtime as worker

    original = worker.run_cycle

    def record(*, dry_run: bool = False):
        stats = original(dry_run=dry_run)
        captured.extend(stats)
        return stats

    monkeypatch.setattr("distribution.workers.runtime.run_cycle", record)
    _drive_immediate_restart(monkeypatch, _account("ancient_knowledge"))
    assert captured[0]["stats"]["published"] == 0
    assert captured[0]["stats"]["skipped_reason"] == "daily_cap"


def test_immediate_restart_keeps_the_daily_cap_and_ledger_dedupe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A zero startup delay still publishes through the shared scheduler.

    ``PINTEREST_PINS_PER_RUN`` is larger than the daily cap, and one of the
    two candidates is already in the ledger. The wake may ask for only the
    daily cap, and that pin must not be the one already recorded.
    """
    import json

    from agents.media.b2_budget import reset_caches
    from agents.posting.pinterest_engine.articles import Article
    from distribution.scheduling.drip import publish_account
    from tests.test_pinterest_vm import FakeS3, _ancient, _library_record, _photo, _prepare_channel

    _prepare_channel(monkeypatch, tmp_path)
    monkeypatch.setenv("B2_USAGE_PATH", str(tmp_path / "usage.json"))
    monkeypatch.setenv("PINTEREST_PINS_PER_RUN", "5")
    monkeypatch.setenv("PINTEREST_PINS_PER_DAY", "2")
    reset_caches()
    site = "https://ancientknowledge.eokviral.com"
    posts = [
        ("post_oak", "oak-island", "Oak Island"),
        ("post_giants", "giants", "Giants"),
    ]
    photo = _photo()
    objects: dict[str, bytes] = {}
    for stem, slug, topic in posts:
        objects[f"ancient_knowledge/library/{stem}.json"] = _library_record(
            slug, topic, f"images/{stem}.jpg"
        )
        objects[f"ancient_knowledge/library/images/{stem}.jpg"] = photo
    ledger_rows = [
        {
            "account": "ancient_knowledge",
            "post_id": "post_oak",
            "title": "Oak Island",
            "pin_id": "old",
            "published_utc": "2026-10-19T08:00:00Z",
        }
    ]
    objects["pinterest/ledger/ancient_knowledge.jsonl"] = "".join(
        json.dumps(row) + "\n" for row in ledger_rows
    ).encode()
    client = FakeS3(objects)
    catalog = [
        Article(url=f"{site}/{slug}", topic=topic, body=topic, slug=slug)
        for _stem, slug, topic in posts
    ]
    monkeypatch.setattr(
        "distribution.workers.runtime._read_account_ledger",
        lambda _account: (ledger_rows, True),
    )
    calls: list[int] = []
    results: list[dict] = []

    def wrapped(account, *, pins=1, dry_run=False, **_kwargs):
        calls.append(pins)
        assert dry_run is False
        result = publish_account(
            account, pins=pins, dry_run=True, client=client, catalog=catalog
        )
        results.append(result)
        return result

    monkeypatch.setattr("distribution.scheduling.drip.publish_account", wrapped)
    _drive_immediate_restart(monkeypatch, _ancient())
    assert calls == [2]
    assert len(results) == 1
    stats = results[0]
    assert stats["published"] == 1
    assert stats["skipped"] >= 1
    assert {pin["post_id"] for pin in stats.get("pins") or []} == {"post_giants"}


def test_seed_does_not_rewrite_a_pin_id_that_already_exists(
    budget: Path, tmp_path: Path
) -> None:
    """An existing pin_id is left untouched, including older duplicate rows."""
    import json

    del budget
    original = [
        {
            "account": "acct",
            "post_id": "",
            "title": "Old title",
            "pin_id": "pin-1",
            "source_hash": "",
            "article": "",
            "seeded": True,
            "published_utc": "2026-09-01T00:00:00Z",
        },
        {
            "account": "acct",
            "post_id": "post-real",
            "title": "Real pin",
            "pin_id": "pin-1",
            "source_hash": "abc",
            "article": "https://example.test/a",
            "seeded": False,
            "published_utc": "2026-10-19T08:00:00Z",
        },
    ]
    cache = tmp_path / "acct.jsonl"
    cache.write_text("".join(json.dumps(row) + "\n" for row in original), encoding="utf-8")
    before = cache.read_bytes()
    client = _Client()
    ledger = PinLedger("acct", client=client, bucket="bucket", cache_path=cache)
    ledger.rows = [dict(row) for row in original]

    added = ledger.seed_board_pins(
        [
            {"id": "pin-1", "title": "Renamed on the board"},
            {"pin_id": "pin-1", "title": "Another spelling"},
        ]
    )
    assert added == 0
    assert client.puts == 0
    assert cache.read_bytes() == before
    assert ledger.rows == original

    dry = PinLedger("acct", client=client, bucket="bucket", cache_path=cache)
    dry.rows = [dict(row) for row in original]
    noted = dry.seed_board_pins([{"id": "pin-9", "title": "Dry run"}], persist=False)
    assert noted == 1
    assert client.puts == 0
    assert cache.read_bytes() == before
    assert dry.rows[-1]["pin_id"] == "pin-9"
    assert dry.rows[-1]["seeded"] is True
    assert dry.rows[:2] == original

    added = ledger.seed_board_pins(
        [
            {"id": "pin-1", "title": "Still the old pin"},
            {"id": "pin-2", "title": "Brand new"},
            {"id": "pin-2", "title": "Same new id twice"},
        ]
    )
    assert added == 1
    assert client.puts == 1
    assert ledger.rows[:2] == original
    assert ledger.rows[2]["pin_id"] == "pin-2"
    assert ledger.rows[2]["seeded"] is True
    assert ledger.rows[2]["title"] == "Brand new"
    stored = [json.loads(line) for line in cache.read_text(encoding="utf-8").splitlines()]
    assert stored[:2] == original
    assert [row["pin_id"] for row in stored].count("pin-1") == 2

    puts = client.puts
    snapshot = cache.read_bytes()
    again = ledger.seed_board_pins(
        [
            {"id": "pin-1", "title": "Renamed"},
            {"id": "pin-2", "title": "Brand new, retitled"},
        ]
    )
    assert again == 0
    assert client.puts == puts
    assert cache.read_bytes() == snapshot
    assert ledger.rows[2]["title"] == "Brand new"
    assert ledger.rows[2]["published_utc"] == stored[2]["published_utc"]


def test_seeded_rows_do_not_move_resume_or_spend_the_daily_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PINTEREST_PINS_PER_RUN", "5")
    monkeypatch.setenv("PINTEREST_PINS_PER_DAY", "2")
    monkeypatch.delenv("PINTEREST_DAY_TZ", raising=False)
    rows = [
        {
            "pin_id": "real",
            "published_utc": "2026-10-20T08:00:00Z",
            "seeded": False,
        },
        {"pin_id": "dup", "published_utc": "2026-10-20T11:30:00Z", "seeded": True},
        {"pin_id": "dup", "published_utc": "2026-10-20T11:40:00Z", "seeded": True},
        {"pin_id": "dup", "published_utc": "2026-10-20T11:45:00Z", "seeded": True},
        {"pin_id": "dup", "published_utc": "2026-10-20T11:50:00Z", "seeded": True},
        {"pin_id": "dup", "published_utc": "2026-10-20T11:55:00Z", "seeded": True},
    ]

    stamp = newest_pin_published(
        [_account("acct")],
        read_rows=lambda _account: (rows, True),
    )
    assert stamp == datetime(2026, 10, 20, 8, 0, tzinfo=timezone.utc)
    assert (
        newest_pin_published(
            [_account("acct")],
            read_rows=lambda _account: (
                [{"published_utc": "2026-10-20T11:00:00Z", "seeded": True, "pin_id": "dup"}],
                True,
            ),
        )
        is None
    )

    allowed = wake_pin_allowance(
        _account("acct"),
        now=_NOW,
        read_rows=lambda _account: (rows, True),
    )
    assert allowed == 1


def test_daily_cap_counts_the_calendar_day_in_pinterest_day_tz(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PINTEREST_PINS_PER_RUN", "4")
    monkeypatch.setenv("PINTEREST_PINS_PER_DAY", "1")
    # 02:30Z is still the 19th in Sao Paulo. 03:30Z is the 20th there, and
    # both stamps are the 20th in UTC.
    late_utc = [{"published_utc": "2026-10-20T02:30:00Z", "seeded": False, "pin_id": "late"}]
    local_today = [{"published_utc": "2026-10-20T03:30:00Z", "seeded": False, "pin_id": "local"}]

    monkeypatch.delenv("PINTEREST_DAY_TZ", raising=False)
    assert (
        wake_pin_allowance(
            _account("acct"),
            now=_NOW,
            read_rows=lambda _account: (late_utc, True),
        )
        == 1
    )
    assert (
        wake_pin_allowance(
            _account("acct"),
            now=_NOW,
            read_rows=lambda _account: (local_today, True),
        )
        == 0
    )

    monkeypatch.setenv("PINTEREST_DAY_TZ", "America/Sao_Paulo")
    assert (
        wake_pin_allowance(
            _account("acct"),
            now=_NOW,
            read_rows=lambda _account: (late_utc, True),
        )
        == 1
    )

    monkeypatch.setenv("PINTEREST_DAY_TZ", "UTC")
    assert (
        wake_pin_allowance(
            _account("acct"),
            now=_NOW,
            read_rows=lambda _account: (late_utc, True),
        )
        == 0
    )

    monkeypatch.setenv("PINTEREST_DAY_TZ", "Not/AZone")
    assert (
        wake_pin_allowance(
            _account("acct"),
            now=_NOW,
            read_rows=lambda _account: (late_utc, True),
        )
        == 1
    )
    monkeypatch.setenv("PINTEREST_DAY_TZ", "   ")
    assert (
        wake_pin_allowance(
            _account("acct"),
            now=_NOW,
            read_rows=lambda _account: (local_today, True),
        )
        == 0
    )
