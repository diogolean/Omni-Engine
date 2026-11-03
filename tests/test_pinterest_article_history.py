# -*- coding: utf-8 -*-
"""Article dedupe, pin-copy provenance, and title length."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from production.providers.llm.text_model import TextResult
from production.formats.pins.pin_copy import keyword_first_title
from distribution.scheduling.inventory import (
    MasterInventory,
    build_caption_regex,
    ensure_writer_pin_copy,
    select_pin_batch,
)
from distribution.scheduling.scheduler import FilenameHistory, PinterestScheduler

_SITE = "https://ancientknowledge.eokviral.com/sumerian/"
_NOW = datetime(2026, 10, 21, 10, 56, tzinfo=timezone.utc)
_OPT_IN = ("USE_CLAUDE", "CLAUDE_MODEL", "CLAUDE_POLISH_MODEL", "LOFI_SCRIPT_MODEL")


def _clear_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _OPT_IN:
        monkeypatch.delenv(name, raising=False)


def _writer_result(caption: str = "Clay still holds the king list.") -> TextResult:
    body = json.dumps(
        {
            "pinterest_title": "Clay tablets outlasted the city",
            "pinterest_caption": caption,
            "visual_hook": "Who kept the clay",
        }
    )
    return TextResult(body, "deepinfra", "deepseek-ai/DeepSeek-V4-Flash", 1, 1, 0.0)


def _library(tmp_path: Path, post_id: str, topic: str, caption: str) -> None:
    library = tmp_path / "library"
    library.mkdir(exist_ok=True)
    (library / f"{post_id}.json").write_text(
        json.dumps(
            {
                "topic": topic,
                "subject_slug": "sumerian",
                "variant_index": 0,
                "humanized_caption": caption,
            }
        ),
        encoding="utf-8",
    )


def _save_inventory(tmp_path: Path, entry: dict) -> None:
    (tmp_path / "master_inventory.json").write_text(
        json.dumps({"schema_version": "2.0", "entries": [entry]}),
        encoding="utf-8",
    )


def test_second_variant_of_the_same_article_is_skipped() -> None:
    six_hours_ago = (_NOW - timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ledger = [
        {
            "published_utc": six_hours_ago,
            "article": "https://www.ancientknowledge.eokviral.com/sumerian/?utm=1",
            "topic": "Sumerian tablets",
            "pin_recorded": True,
        }
    ]
    candidates = [
        {
            "post_id": "post_sumerian_b",
            "topic": "Sumerian king list",
            "target_url": _SITE,
            "local_image_path": "images/variant-b.jpg",
        },
        {
            "post_id": "post_gobekli",
            "topic": "Gobekli Tepe",
            "target_url": "https://ancientknowledge.eokviral.com/gobekli/",
            "local_image_path": "images/gobekli.jpg",
        },
    ]
    batch, skipped = select_pin_batch(candidates, [], ledger, 1, now=_NOW)
    assert skipped == 1
    assert [entry["post_id"] for entry in batch] == ["post_gobekli"]


def test_article_url_only_row_blocks_the_same_article() -> None:
    """A history row that only names article_url must match target_url."""
    from distribution.scheduling.inventory import recent_match_keys

    row = {
        "published_utc": "2026-10-21T04:56:00Z",
        "article_url": "https://ancientknowledge.eokviral.com/sumerian/",
    }
    keys = recent_match_keys([row], now=_NOW)
    assert "url:ancientknowledge.eokviral.com/sumerian" in keys
    for field in ("target_url", "link", "url", "article"):
        keys = recent_match_keys(
            [
                {
                    "published_utc": "2026-10-21T04:56:00Z",
                    field: "https://www.ancientknowledge.eokviral.com/sumerian/?utm=1",
                }
            ],
            now=_NOW,
        )
        assert "url:ancientknowledge.eokviral.com/sumerian" in keys
    candidates = [
        {
            "post_id": "post_20260719_223247_440964_v01",
            "topic": "Sumerian king list",
            "target_url": "https://ancientknowledge.eokviral.com/sumerian/",
        },
        {
            "post_id": "post_gobekli",
            "topic": "Gobekli Tepe",
            "target_url": "https://ancientknowledge.eokviral.com/gobekli/",
        },
    ]
    batch, skipped = select_pin_batch(candidates, [row], [], 1, now=_NOW)
    assert skipped == 1
    assert [entry["post_id"] for entry in batch] == ["post_gobekli"]


def test_post_id_only_row_uses_the_inventory_article() -> None:
    """Pre-#62 rows can be post_id alone. The inventory entry supplies the URL."""
    posted = "post_20260719_215835_642753_v01"
    nxt = "post_20260719_223247_440964_v01"
    row = {
        "published_utc": "2026-10-21T02:01:00Z",
        "post_id": posted,
    }
    inventory = [
        {
            "post_id": posted,
            "topic": "Sumerian tablets",
            "target_url": "https://ancientknowledge.eokviral.com/sumerian/",
        }
    ]
    candidates = [
        {
            "post_id": nxt,
            "topic": "Sumerian king list",
            "target_url": "https://www.ancientknowledge.eokviral.com/sumerian/",
        },
        {
            "post_id": "post_gobekli",
            "topic": "Gobekli Tepe",
            "target_url": "https://ancientknowledge.eokviral.com/gobekli/",
        },
    ]
    batch, skipped = select_pin_batch(
        candidates, [row], [], 1, now=_NOW, inventory=inventory
    )
    assert skipped == 1
    assert [entry["post_id"] for entry in batch] == ["post_gobekli"]


def test_old_history_line_without_a_url_uses_the_inventory(tmp_path: Path) -> None:
    """Pre-#62 history is topic and post_id. The next variant has another topic."""
    posted = "post_20260719_215835_642753_v01"
    nxt = "post_20260719_223247_440964_v01"
    (tmp_path / "scheduled_history.txt").write_text(
        "2026-10-21T02:01:00Z | sumerian-a.jpg | "
        f"{posted} | pin-2301 | Sumerian tablets\n",
        encoding="utf-8",
    )
    rows = FilenameHistory(tmp_path).article_rows()
    assert rows[0]["url"] == ""
    assert rows[0]["post_id"] == posted
    assert rows[0]["topic"] == "Sumerian tablets"
    inventory = [
        {
            "post_id": posted,
            "topic": "Sumerian tablets",
            "target_url": "https://ancientknowledge.eokviral.com/sumerian/",
        }
    ]
    candidates = [
        {
            "post_id": nxt,
            "topic": "Sumerian king list",
            "target_url": "https://www.ancientknowledge.eokviral.com/sumerian/",
        },
        {
            "post_id": "post_gobekli",
            "topic": "Gobekli Tepe",
            "target_url": "https://ancientknowledge.eokviral.com/gobekli/",
        },
    ]
    batch, skipped = select_pin_batch(
        candidates, rows, [], 1, now=_NOW, inventory=inventory
    )
    assert skipped == 1
    assert [entry["post_id"] for entry in batch] == ["post_gobekli"]


def test_shared_generic_words_do_not_block() -> None:
    """Ancient, lost, and city appear in many topics. Only the full topic matches."""
    history = [
        {
            "published_utc": "2026-10-21T04:56:00Z",
            "topic": "The ancient lost city",
            "article_url": "https://ancientknowledge.eokviral.com/paititi/",
        }
    ]
    candidates = [
        {
            "post_id": "tartaria",
            "topic": "Another ancient lost city",
            "target_url": "https://ancientknowledge.eokviral.com/tartaria/",
        },
        {
            "post_id": "underwater",
            "topic": "Ancient lost city under the sea",
            "target_url": "https://ancientknowledge.eokviral.com/underwater-cities/",
        },
    ]
    batch, skipped = select_pin_batch(candidates, history, [], 2, now=_NOW)
    assert skipped == 0
    assert [entry["post_id"] for entry in batch] == ["tartaria", "underwater"]


def test_same_domain_different_paths_do_not_block() -> None:
    """The host is not the article. A different path, or no path, stays free."""
    from distribution.scheduling.inventory import normalize_article_url, recent_match_keys

    assert normalize_article_url("https://ancientknowledge.eokviral.com/") == ""
    assert normalize_article_url("https://ancientknowledge.eokviral.com") == ""
    history = [
        {
            "published_utc": "2026-10-21T04:56:00Z",
            "topic": "Sumerian tablets",
            "article": "https://www.ancientknowledge.eokviral.com/sumerian/?utm=1",
        },
        {
            "published_utc": "2026-10-21T03:00:00Z",
            "url": "https://ancientknowledge.eokviral.com/",
        },
    ]
    keys = recent_match_keys(history, now=_NOW)
    assert "url:ancientknowledge.eokviral.com" not in keys
    assert "url:ancientknowledge.eokviral.com/sumerian" in keys
    candidates = [
        {
            "post_id": "tartaria",
            "topic": "Tartaria and the mudflood",
            "target_url": "https://ancientknowledge.eokviral.com/tartaria/",
        },
        {
            "post_id": "underwater",
            "topic": "Ancient underwater cities",
            "target_url": "https://ancientknowledge.eokviral.com/underwater-cities/",
        },
    ]
    batch, skipped = select_pin_batch(candidates, history, [], 2, now=_NOW)
    assert skipped == 0
    assert [entry["post_id"] for entry in batch] == ["tartaria", "underwater"]


def test_article_outside_the_window_can_publish(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PINTEREST_DEDUP_RECENT_DAYS", "7")
    monkeypatch.setenv("PINTEREST_DEDUP_RECENT_PINS", "30")
    old = (_NOW - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ledger = [
        {
            "published_utc": old,
            "article": _SITE,
            "topic": "Sumerian tablets",
            "pin_recorded": True,
        }
    ]
    candidates = [
        {
            "post_id": "post_sumerian_b",
            "topic": "A different working",
            "target_url": _SITE,
        }
    ]
    batch, skipped = select_pin_batch(candidates, [], ledger, 1, now=_NOW)
    assert skipped == 0
    assert batch[0]["post_id"] == "post_sumerian_b"


def test_history_file_records_the_article_url(tmp_path: Path) -> None:
    history = FilenameHistory(tmp_path)
    history.record(
        "variant-a.jpg",
        "post_sumerian_a",
        "pin-1",
        "Sumerian tablets",
        timestamp="2026-10-21T04:56:00Z",
        article=_SITE,
    )
    reloaded = FilenameHistory(tmp_path)
    assert "variant-a.jpg" in reloaded
    rows = reloaded.article_rows()
    assert rows[0]["url"] == _SITE
    batch, skipped = select_pin_batch(
        [
            {
                "post_id": "post_sumerian_b",
                "topic": "Another angle",
                "target_url": "https://ancientknowledge.eokviral.com/sumerian",
            },
            {
                "post_id": "post_nazca",
                "topic": "Nazca lines",
                "target_url": "https://ancientknowledge.eokviral.com/nazca/",
            },
        ],
        rows,
        [],
        1,
        now=_NOW,
    )
    assert skipped == 1
    assert batch[0]["post_id"] == "post_nazca"


def test_scheduler_selects_the_next_article_and_refreshes_copy() -> None:
    import inspect

    source = inspect.getsource(PinterestScheduler.run)
    assert "select_pin_batch" in source
    assert "ensure_writer_pin_copy" in source
    assert "article=" in source


def test_missing_or_claude_copy_is_regenerated_with_writer_primary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clear_opt_in(monkeypatch)
    calls: list[str] = []

    def _writer(prompt: str, **_kwargs: object) -> TextResult:
        calls.append(prompt)
        return _writer_result()

    monkeypatch.setattr("agents.mcp.text_model.complete_writer_primary", _writer)
    _library(tmp_path, "post_oak", "Oak Island", "The money pit is still open.")
    _save_inventory(
        tmp_path,
        {
            "post_id": "post_oak",
            "topic": "Oak Island",
            "pinterest_title": "Claude: Oak Island pit",
            "pinterest_caption": "Old Claude caption.",
            "visual_hook": "OLD HOOK",
            "copy_provider": "anthropic",
            "copy_model": "claude-sonnet-4-5",
            "copy_generated_at": "2026-10-19T00:00:00Z",
        },
    )
    data = MasterInventory(outputs_dir=tmp_path).build(use_ai=True, dry_run=True)
    entry = data["entries"][0]
    assert calls
    assert "Oak Island" in calls[0]
    assert entry["copy_provider"] == "deepinfra"
    assert entry["copy_model"] == "deepseek-ai/DeepSeek-V4-Flash"
    assert entry["copy_generated_at"]
    assert "Clay still holds the king list." in entry["pinterest_caption"]
    assert "claude" not in entry["copy_model"]

    bare = {
        "topic": "Oak Island",
        "original_caption": "The money pit is still open.",
        "pinterest_title": "Saved title",
        "pinterest_caption": "Saved caption with no stamp.",
        "variant_index": 0,
    }
    assert ensure_writer_pin_copy(bare, use_ai=True) is True
    assert bare["copy_provider"] == "deepinfra"
    assert bare["copy_model"] == "deepseek-ai/DeepSeek-V4-Flash"


def test_rebuild_keeps_a_saved_article_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ancient Knowledge rebuilds must not wipe the article already on the entry."""
    from production.formats.pins import config as cfg

    _clear_opt_in(monkeypatch)
    monkeypatch.setattr(cfg, "CHANNEL_ID", cfg.CHANNEL_ID, raising=False)
    cfg.configure(channel_id="ancient_knowledge", inventory_dir=tmp_path)

    def _writer_off(*_args: object, **_kwargs: object) -> TextResult:
        raise RuntimeError("writer off")

    monkeypatch.setattr("agents.mcp.text_model.complete_writer_primary", _writer_off)
    _library(tmp_path, "post_20260719_215835_642753_v01", "Sumerian tablets", "Clay.")
    _save_inventory(
        tmp_path,
        {
            "post_id": "post_20260719_215835_642753_v01",
            "topic": "Sumerian tablets",
            "target_url": "https://ancientknowledge.eokviral.com/sumerian/",
            "pinterest_title": "Sumerian tablets",
            "pinterest_caption": "Clay still holds the list.",
            "visual_hook": "WHO KEPT THE CLAY",
            "copy_provider": "deepinfra",
            "copy_model": "deepseek-ai/DeepSeek-V4-Flash",
            "copy_generated_at": "2026-10-20T12:00:00Z",
        },
    )
    data = MasterInventory(outputs_dir=tmp_path).build(use_ai=True, dry_run=True)
    assert (
        data["entries"][0]["target_url"]
        == "https://ancientknowledge.eokviral.com/sumerian/"
    )


def test_trusted_writer_copy_is_kept(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _clear_opt_in(monkeypatch)

    def _boom(*_args: object, **_kwargs: object) -> TextResult:
        raise AssertionError("writer was called for stamped copy")

    monkeypatch.setattr("agents.mcp.text_model.complete_writer_primary", _boom)
    _library(tmp_path, "post_oak", "Oak Island", "The money pit is still open.")
    _save_inventory(
        tmp_path,
        {
            "post_id": "post_oak",
            "topic": "Oak Island",
            "pinterest_title": "Oak Island pit",
            "pinterest_caption": "Stamped caption.",
            "visual_hook": "WHO DUG",
            "copy_provider": "deepinfra",
            "copy_model": "deepseek-ai/DeepSeek-V4-Flash",
            "copy_generated_at": "2026-10-20T12:00:00Z",
        },
    )
    data = MasterInventory(outputs_dir=tmp_path).build(use_ai=True, dry_run=True)
    entry = data["entries"][0]
    assert entry["pinterest_caption"] == "Stamped caption."
    assert entry["copy_provider"] == "deepinfra"
    assert entry["copy_model"] == "deepseek-ai/DeepSeek-V4-Flash"


def test_writer_failure_uses_templates(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_opt_in(monkeypatch)

    def _boom(*_args: object, **_kwargs: object) -> TextResult:
        raise RuntimeError("writer down")

    monkeypatch.setattr("agents.mcp.text_model.complete_writer_primary", _boom)
    entry = {
        "topic": "Oak Island",
        "original_caption": "The money pit is still open.",
        "pinterest_title": "Claude title",
        "pinterest_caption": "Claude caption.",
        "copy_provider": "anthropic",
        "copy_model": "claude-3-5-sonnet-20241022",
        "copy_generated_at": "2026-10-19T00:00:00Z",
        "variant_index": 0,
    }
    assert ensure_writer_pin_copy(entry, use_ai=True) is True
    assert entry["pinterest_caption"] == build_caption_regex("The money pit is still open.", 0)
    assert entry["copy_provider"] == "template"


def test_title_does_not_repeat_the_topic_or_cut_a_word() -> None:
    titled = keyword_first_title(
        "Clay records from the deep mound: Sumerian Tablets still do not match the flood",
        "Sumerian Tablets",
    )
    assert titled.lower().count("sumerian tablets") == 1
    assert not titled.lower().startswith("sumerian tablets: clay")

    prefix = "Sumerian Tablets clay "
    while len(prefix) < 90:
        prefix += "word "
    long_word = "UnbrokenTokenXXXX"
    fitted = keyword_first_title(prefix + long_word + " tail", "Sumerian Tablets")
    assert len(prefix) < 100 < len(prefix) + len(long_word)
    assert len(fitted) <= 100
    assert long_word not in fitted
    assert not any(fitted.endswith(long_word[:index]) for index in range(1, len(long_word)))
    assert keyword_first_title(
        "Hidden truth you won't believe", "Baalbek trilithon"
    ).lower().startswith("baalbek")
