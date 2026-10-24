# -*- coding: utf-8 -*-
"""The merged Pinterest publisher keeps the old dedup keys and ledger path.

Two stores stay separate, and both keep the keys they had before the move:

* ``$OUTPUT_PATH/_omni/publish_ledger.json`` keyed by ``Content.idempotency_key``
* the B2 ``PinLedger`` at ``pinterest/ledger/<account>.jsonl``, matched on
  post id, normalized title, or source-image hash
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import agents.posting.pinterest_engine.ledger as legacy_ledger
import agents.posting.pinterest_engine.publisher as legacy_publisher
import distribution.publishers.api.pinterest as pinterest_api
import distribution.scheduling.ledger as pin_ledger
import distribution.workers.__main__ as worker_main
import distribution.workers.runtime as runtime
from distribution.publishers.base import (
    Account,
    Content,
    FileIdempotencyStore,
    PublishResult,
    Publisher,
    default_ledger_path,
)
from distribution.scheduling.ledger import (
    PinLedger,
    ledger_object_key,
    normalize_title,
    warn_stale_reservations,
)
from distribution.workers.runtime import (
    newest_pin_published,
    resolve_startup_delay,
    save_channel_wake,
    wake_pin_allowance,
)


def test_old_and_new_pinterest_modules_are_one_object() -> None:
    assert legacy_publisher is pinterest_api
    assert legacy_publisher.PinterestPublisher is pinterest_api.PinterestPublisher
    assert legacy_publisher.PinterestAccountPublisher is pinterest_api.PinterestAccountPublisher
    assert legacy_ledger is pin_ledger
    assert legacy_ledger.ledger_object_key is ledger_object_key
    assert legacy_ledger.normalize_title is normalize_title
    assert worker_main.main is runtime.main


def test_default_ledger_path_stays_under_omni(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OMNI_LEDGER_PATH", raising=False)
    monkeypatch.delenv("OUTPUT_PATH", raising=False)
    path = default_ledger_path()
    repo = Path(__file__).resolve().parents[1]
    assert path == repo / "outputs" / "_omni" / "publish_ledger.json"


def test_output_path_and_ledger_override_keep_the_same_names(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("OMNI_LEDGER_PATH", raising=False)
    monkeypatch.setenv("OUTPUT_PATH", str(tmp_path))
    assert default_ledger_path() == tmp_path / "_omni" / "publish_ledger.json"

    override = tmp_path / "custom" / "publish_ledger.json"
    monkeypatch.setenv("OMNI_LEDGER_PATH", str(override))
    assert default_ledger_path() == override


def test_protocol_idempotency_key_is_the_content_key(tmp_path: Path) -> None:
    key = "ancient_knowledge:gobekli-01"
    store = FileIdempotencyStore(tmp_path / "_omni" / "publish_ledger.json")

    class _Once(Publisher):
        platform = "pinterest"

        def __init__(self) -> None:
            super().__init__(store=store, max_retries=1, sleeper=lambda _s: None)
            self.calls: list[str] = []

        def publish_once(self, content: Content, account: Account) -> PublishResult:
            self.calls.append(content.idempotency_key)
            return PublishResult(
                ok=True,
                platform=self.platform,
                idempotency_key=content.idempotency_key,
                external_id="pin_9",
            )

    account = Account(branding="ancient_knowledge", platform="pinterest", account_id="ancient_knowledge")
    content = Content(idempotency_key=key, title="Stone circle")
    publisher = _Once()
    first = publisher.publish(content, account)
    second = publisher.publish(content, account)
    assert publisher.calls == [key]
    assert first.external_id == "pin_9"
    assert first.duplicate is False
    assert second.duplicate is True
    assert second.skipped is True
    assert second.external_id == "pin_9"
    raw = json.loads(store.path.read_text(encoding="utf-8"))
    assert list(raw) == [key]
    assert raw[key]["idempotency_key"] == key
    assert raw[key]["external_id"] == "pin_9"


def test_account_adapter_copies_the_idempotency_key_onto_the_pin_record() -> None:
    record = pinterest_api._record_from_content(
        Content(
            idempotency_key="ancient_knowledge:post-7",
            title="Stone",
            body="A quiet note",
            link="https://example.test/stone",
        )
    )
    assert record["post_id"] == "ancient_knowledge:post-7"
    assert record["pinterest_title"] == "Stone"
    assert record["pinterest_caption"] == "A quiet note"
    assert record["target_url"] == "https://example.test/stone"

    kept = pinterest_api._record_from_content(
        Content(
            idempotency_key="new-key",
            payload={"post_id": "already-set", "pinterest_title": "Kept"},
        )
    )
    assert kept["post_id"] == "already-set"
    assert kept["pinterest_title"] == "Kept"


def test_pin_ledger_dedup_keys_are_unchanged(tmp_path: Path) -> None:
    assert ledger_object_key("ancient_knowledge") == "pinterest/ledger/ancient_knowledge.jsonl"
    assert ledger_object_key("Anna Protocol") == "pinterest/ledger/Anna_Protocol.jsonl"
    assert normalize_title("  Stone   Circle!!! ") == "stone circle"

    ledger = PinLedger("ancient_knowledge", cache_path=tmp_path / "ancient_knowledge.jsonl")
    ledger.rows = [
        {
            "post_id": "ancient_knowledge:post-7",
            "title": "Stone Circle",
            "source_hash": "abc123",
            "pin_recorded": True,
        }
    ]
    assert ledger.seen(post_id="ancient_knowledge:post-7") is True
    assert ledger.seen(title="stone   circle") is True
    assert ledger.seen(source_hash="abc123") is True
    assert ledger.seen(post_id="other", title="other", source_hash="nope") is False

    ledger.rows = [
        {
            "post_id": "ancient_knowledge:post-7",
            "title": "Stone Circle",
            "source_hash": "abc123",
            "pin_recorded": False,
            "facebook_exported": True,
        }
    ]
    assert ledger.seen(post_id="ancient_knowledge:post-7") is False
    assert ledger.seen(title="Stone Circle") is False
    assert ledger.seen(source_hash="abc123") is False


def test_reservation_blocks_a_second_pin_until_it_is_released(tmp_path: Path) -> None:
    """A row written before the API call is visible to seen(), with no pin id yet."""
    ledger = PinLedger("ancient_knowledge", cache_path=tmp_path / "ancient_knowledge.jsonl")
    ledger.rows = [
        {
            "post_id": "facebook-note",
            "title": "Stone Circle",
            "source_hash": "note-hash",
            "pin_recorded": False,
            "facebook_exported": True,
            "seeded": False,
        },
        {
            "post_id": "",
            "title": "Board history",
            "pin_id": "pin-seed",
            "source_hash": "seed-hash",
            "seeded": True,
        },
    ]
    ledger.reserve(
        post_id="ancient_knowledge:post-9",
        title="New Stone",
        source_hash="img-9",
        article="https://example.test/new",
    )
    stored = [
        json.loads(line)
        for line in (tmp_path / "ancient_knowledge.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(stored) == 3
    reserved = stored[-1]
    assert reserved["pin_id"] == ""
    assert reserved["reserved"] is True
    assert reserved.get("pin_recorded") is not False
    assert ledger.seen(post_id="ancient_knowledge:post-9") is True
    assert ledger.seen(title="New Stone") is True
    assert ledger.seen(source_hash="img-9") is True

    ledger.reserve(
        post_id="ancient_knowledge:post-9",
        title="New Stone",
        source_hash="img-9",
    )
    assert len(ledger.rows) == 3

    ledger.release(post_id="ancient_knowledge:post-9", source_hash="img-9")
    assert ledger.seen(post_id="ancient_knowledge:post-9") is False
    assert ledger.seen(title="New Stone") is False
    assert ledger.seen(source_hash="img-9") is False
    assert any(row.get("pin_recorded") is False for row in ledger.rows)
    assert any(row.get("seeded") is True and row.get("pin_id") == "pin-seed" for row in ledger.rows)
    assert len(ledger.rows) == 2


def test_complete_fills_the_reserved_row_without_a_second_append(tmp_path: Path) -> None:
    ledger = PinLedger("ancient_knowledge", cache_path=tmp_path / "ancient_knowledge.jsonl")
    ledger.reserve(
        post_id="ancient_knowledge:post-9",
        title="New Stone",
        source_hash="img-9",
        article="https://example.test/new",
    )
    stamp = ledger.rows[0]["published_utc"]
    ledger.complete(
        post_id="ancient_knowledge:post-9",
        pin_id="pin-live",
        title="New Stone",
        source_hash="img-9",
        article="https://example.test/new",
        facebook_exported=True,
    )
    assert len(ledger.rows) == 1
    row = ledger.rows[0]
    assert row["pin_id"] == "pin-live"
    assert row["reserved"] is False
    assert row["facebook_exported"] is True
    assert row["published_utc"] == stamp
    assert ledger.seen(post_id="ancient_knowledge:post-9") is True
    ledger.release(post_id="ancient_knowledge:post-9", source_hash="img-9")
    assert len(ledger.rows) == 1
    assert ledger.rows[0]["pin_id"] == "pin-live"


def test_already_posted_record_does_not_call_pinterest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agents.posting.pinterest_engine import config as cfg

    monkeypatch.setattr(cfg, "DOTENV_LOADED_FROM_FILE", True, raising=False)
    monkeypatch.setenv("PINTEREST_BOARD_NAME", "Board")
    publisher = pinterest_api.PinterestPublisher(
        access_token="tok",
        board_id="board-1",
        require_board=False,
    )

    def boom(*_args, **_kwargs):
        raise AssertionError("already-posted pin must not call Pinterest")

    monkeypatch.setattr(publisher, "_post_with_retry", boom)
    result = publisher.publish(
        {
            "post_id": "ancient_knowledge:post-7",
            "pinterest_caption": "hello",
            "publication_status": {
                "posted_on_pinterest": True,
                "pinterest_pin_id": "99",
            },
        },
        b"\xff\xd8jpeg",
    )
    assert result is None


def test_worker_cli_keeps_role_once_dry_run_account_and_pins() -> None:
    """Compose and the image call ``python -m distribution.workers`` with these flags."""
    import subprocess

    proc = subprocess.run(
        [sys.executable, "-m", "distribution.workers", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    for flag in ("--role", "--once", "--dry-run", "--account", "--pins"):
        assert flag in proc.stdout
    missing = subprocess.run(
        [sys.executable, "-m", "omni.worker", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert missing.returncode != 0


def test_stale_reservation_warns_and_a_fresh_one_does_not(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A reservation older than one hour with no pin_id is logged and kept."""
    now = datetime(2026, 10, 20, 15, 0, tzinfo=timezone.utc)
    stale = (now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    fresh = (now - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = [
        {"post_id": "stale-post", "pin_id": "", "reserved": True, "published_utc": stale},
        {"post_id": "fresh-post", "pin_id": "", "reserved": True, "published_utc": fresh},
        {
            "post_id": "facebook-note",
            "pin_id": "",
            "reserved": True,
            "pin_recorded": False,
            "published_utc": stale,
        },
        {"post_id": "seeded-row", "pin_id": "", "reserved": True, "seeded": True, "published_utc": stale},
        {"post_id": "finished", "pin_id": "pin-1", "reserved": False, "published_utc": stale},
    ]
    text = "".join(json.dumps(row) + "\n" for row in rows)
    monkeypatch.setattr(
        "agents.media.b2_client.read_object_outcome",
        lambda *_args, **_kwargs: (text, "ok"),
    )
    monkeypatch.setattr(pin_ledger, "_utc_now", lambda: now)
    ledger = PinLedger("ancient_knowledge", cache_path=tmp_path / "ancient_knowledge.jsonl")
    with caplog.at_level(logging.WARNING, logger="distribution.scheduling.ledger"):
        loaded, reachable = ledger.read_rows()
    assert reachable is True
    assert len(loaded) == 5
    warnings = [record.getMessage() for record in caplog.records]
    assert len(warnings) == 1
    assert "stale-post" in warnings[0]
    assert "more than 1h ago" in warnings[0]
    assert "fresh-post" not in warnings[0]
    assert "facebook-note" not in warnings[0]
    assert "seeded-row" not in warnings[0]
    assert warn_stale_reservations(rows, account_id="ancient_knowledge", now=now) == 1


def test_open_reservation_counts_toward_the_daily_cap_and_catchup_skip(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An empty pin_id still consumes today's cap and can cover a missed wake.

    A reservation inside the 5-minute margin before the saved wake skips
    catch-up. One older than that margin does not. Seeded rows and Facebook
    notes do not consume the daily cap.
    """
    monkeypatch.setattr(
        "distribution.workers.runtime.scheduler_state_path",
        lambda: tmp_path / "next_wake.json",
    )
    monkeypatch.setattr(
        "distribution.workers.runtime._scheduler_channel_ids",
        lambda: ["ancient_knowledge"],
    )
    monkeypatch.setenv("PINTEREST_PINS_PER_DAY", "1")
    monkeypatch.setenv("PINTEREST_PINS_PER_RUN", "1")
    monkeypatch.setenv("PINTEREST_DAY_TZ", "UTC")
    monkeypatch.setenv("MIN_INTERVAL_HOURS", "4")
    monkeypatch.setenv("MAX_INTERVAL_HOURS", "4")

    now = datetime(2026, 10, 20, 15, 0, tzinfo=timezone.utc)
    wake = now - timedelta(minutes=10)
    reserved_at = wake - timedelta(minutes=4)
    reservation = {
        "post_id": "ancient_knowledge:post-9",
        "pin_id": "",
        "reserved": True,
        "seeded": False,
        "published_utc": reserved_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    account = Account(
        branding="ancient_knowledge",
        platform="pinterest",
        account_id="ancient_knowledge",
    )

    def reserved_rows(_account: Account) -> tuple[list[dict], bool]:
        return [reservation], True

    assert wake_pin_allowance(account, now=now, read_rows=reserved_rows) == 0

    def ignored_rows(_account: Account) -> tuple[list[dict], bool]:
        stamp = reservation["published_utc"]
        return [
            {"pin_id": "", "seeded": True, "published_utc": stamp},
            {"pin_id": "", "pin_recorded": False, "published_utc": stamp},
        ], True

    assert wake_pin_allowance(account, now=now, read_rows=ignored_rows) == 1

    stamp = newest_pin_published([account], read_rows=reserved_rows)
    assert stamp == reserved_at
    save_channel_wake("ancient_knowledge", wake)
    delay, reason = resolve_startup_delay(stamp, now=now, reader=None)
    assert reason == "draw"
    assert delay > 0

    older = {
        **reservation,
        "published_utc": (wake - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    old_stamp = newest_pin_published([account], read_rows=lambda _account: ([older], True))
    save_channel_wake("ancient_knowledge", wake)
    delay_old, reason_old = resolve_startup_delay(old_stamp, now=now, reader=None)
    assert reason_old == "missed"
    assert delay_old == 0.0
