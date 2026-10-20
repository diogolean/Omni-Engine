# -*- coding: utf-8 -*-
"""The merged Pinterest publisher keeps the old dedup keys and ledger path.

Two stores stay separate, and both keep the keys they had before the move:

* ``$OUTPUT_PATH/_omni/publish_ledger.json`` keyed by ``Content.idempotency_key``
* the B2 ``PinLedger`` at ``pinterest/ledger/<account>.jsonl``, matched on
  post id, normalized title, or source-image hash
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import accounts.brandings as brandings
import accounts.pinterest as pinterest_accounts
import accounts.tokens as tokens
import agents.posting.pinterest_engine.ledger as legacy_ledger
import agents.posting.pinterest_engine.publisher as legacy_publisher
import distribution.publishers.api.pinterest as pinterest_api
import distribution.publishers.base as base
import distribution.scheduling.drip as drip
import distribution.scheduling.ledger as pin_ledger
import distribution.workers.__main__ as worker_main
import distribution.workers.runtime as runtime
import omni.accounts as omni_accounts
import omni.adapters.pinterest as omni_pinterest
import omni.config as omni_config
import omni.models as omni_models
import omni.pinterest_accounts as omni_pinterest_accounts
import omni.pinterest_drip as omni_drip
import omni.publisher as omni_publisher
import omni.worker as omni_worker
from distribution.publishers.base import (
    Account,
    Content,
    FileIdempotencyStore,
    PublishResult,
    Publisher,
    default_ledger_path,
)
from distribution.scheduling.ledger import PinLedger, ledger_object_key, normalize_title


def test_old_and_new_pinterest_modules_are_one_object() -> None:
    assert legacy_publisher is pinterest_api is omni_pinterest
    assert legacy_publisher.PinterestPublisher is pinterest_api.PinterestPublisher
    assert legacy_publisher.PinterestAccountPublisher is pinterest_api.PinterestAccountPublisher
    assert omni_models is omni_publisher is base
    assert omni_drip is drip
    assert omni_worker is runtime
    assert omni_config is brandings
    assert omni_accounts is tokens
    assert omni_pinterest_accounts is pinterest_accounts
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
    """Compose calls ``distribution.workers``. ``omni.worker`` is the same fallback."""
    import subprocess

    for module in ("distribution.workers", "omni.worker"):
        proc = subprocess.run(
            [sys.executable, "-m", module, "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        for flag in ("--role", "--once", "--dry-run", "--account", "--pins"):
            assert flag in proc.stdout
