# -*- coding: utf-8 -*-
"""ADR 0008 M3: ContentItem v1 is the library contract.

Production writes it. Distribution reads it through shared.contracts.
The other three stored shapes still load. Old import paths are the same modules.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import modules.distribution_contract as distribution_contract
import modules.durable_store as durable_store
import modules.memory as memory_pkg
import modules.memory.client as memory_client
import shared.contracts as contracts
import shared.memory as shared_memory
import shared.memory.client as shared_memory_client
import shared.store as store
from agents.media.content_library import build_library_metadata
from shared.contracts import (
    CONTENT_ITEM_SCHEMA_VERSION,
    DistributionRecord,
    content_item_from_any,
    load_content_items,
    new_content_item,
    update_publish_status,
    upgrade_content_library,
)

_REPO = Path(__file__).resolve().parents[1]
_PRODUCTION_ROOTS = ("engine", "modules", "agents.media", "agents.writer", "core")


def test_store_contract_and_memory_shims_are_the_shared_modules() -> None:
    assert distribution_contract is contracts
    assert durable_store is store
    assert memory_pkg is shared_memory
    assert memory_client is shared_memory_client
    assert distribution_contract.ContentItem is contracts.ContentItem
    assert durable_store.hydrate_state_file is store.hydrate_state_file


def test_production_write_is_content_item_v1(tmp_path: Path) -> None:
    row = build_library_metadata(
        topic="Gobekli Tepe",
        final_caption="The pillars predate pottery.",
        imgbb_url="https://img.example/pillar.jpg",
        video_path="outputs/ancient_knowledge/reel.mp4",
        channel_id="ancient_knowledge",
        post_type="ECONOMIC_REEL",
        title="Pillars older than pottery",
        carousel_images=[{"url": "https://img.example/slide.jpg"}],
    )
    assert row["schema_version"] == CONTENT_ITEM_SCHEMA_VERSION
    assert row["topic"] == "Gobekli Tepe"
    assert row["final_caption"] == "The pillars predate pottery."
    assert row["video_path"] == "outputs/ancient_knowledge/reel.mp4"
    assert row["channel_id"] == "ancient_knowledge"
    assert row["carousel_images"] == [{"url": "https://img.example/slide.jpg"}]
    assert row["base_metadata"]["title"] == "Pillars older than pottery"

    path = tmp_path / "content_library.json"
    path.write_text(json.dumps([row]), encoding="utf-8")
    item = load_content_items(path, channel_id="ancient_knowledge")[0]
    assert item.schema_version == 1
    assert item.topic == "Gobekli Tepe"
    assert item.final_caption.startswith("The pillars")


def test_distribution_read_does_not_import_production() -> None:
    tree = ast.parse(Path(contracts.__file__).read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    for name in imported:
        assert not name.startswith(_PRODUCTION_ROOTS), name
        assert not name.startswith(("omni", "agents.posting", "channels_config"))

    banned = ("modules.durable_store", "modules.distribution_contract", "modules.memory")
    for root_name in ("agents/posting", "distribution", "accounts", "omni"):
        for path in (_REPO / root_name).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for name in banned:
                assert name not in text, f"{path} imports {name}"
    publish = (_REPO / "agents/posting/publish_existing.py").read_text(encoding="utf-8")
    assert "load_content_items" in publish
    assert "shared.contracts" in publish


def test_old_shapes_adapt_to_content_item_v1() -> None:
    record = DistributionRecord(
        schema_version="1.0",
        channel_id="aiwake",
        post_type="debate",
        topic="session",
        video_path="clips/a.mp4",
        final_caption="A full record.",
        base_metadata={"title": "Session", "caption": "A full record."},
        platform_overrides={"x": {"caption": "A full record."}},
    )
    from_record = content_item_from_any(record.to_library_row())
    assert from_record.schema_version == 1
    assert from_record.channel_id == "aiwake"
    assert from_record.base_metadata.title == "Session"

    publish = {
        "topic": "Baghdad Battery",
        "final_caption": "A jar and a scroll.",
        "imgbb_url": "https://img.example/jar.jpg",
        "video_path": "reel.mp4",
        "timestamp": "2026-01-02T03:04:05+00:00",
        "youtube_video_id": "abc123",
    }
    from_publish = content_item_from_any(publish, channel_id="ancient_knowledge")
    assert from_publish.schema_version == 1
    assert from_publish.topic == "Baghdad Battery"
    assert from_publish.final_caption == "A jar and a scroll."
    assert (from_publish.model_extra or {})["youtube_video_id"] == "abc123"

    omni = {
        "idempotency_key": "pin-1",
        "title": "Trilithon",
        "body": "Three stones.",
        "link": "https://example.test/trilithon",
        "media_path": "images/trilithon.jpg",
        "payload": {"channel_id": "ancient_knowledge", "topic": "Baalbek"},
    }
    from_omni = content_item_from_any(omni)
    assert from_omni.schema_version == 1
    assert from_omni.asset_id == "pin-1"
    assert from_omni.topic == "Baalbek"
    assert from_omni.video_path == "images/trilithon.jpg"
    assert from_omni.platform_overrides.pinterest.destination_url == "https://example.test/trilithon"

    pin = {
        "subject_slug": "gobekli",
        "pinterest_title": "Carved Pillars",
        "topic": "Gobekli Tepe",
        "local_image_path": "assets/gobekli.jpg",
        "target_url": "https://example.test/gobekli",
        "variant_index": 2,
    }
    from_pin = content_item_from_any(pin, channel_id="ancient_knowledge")
    assert from_pin.schema_version == 1
    assert from_pin.base_metadata.title == "Carved Pillars"
    assert from_pin.video_path == "assets/gobekli.jpg"
    assert from_pin.platform_overrides.pinterest.destination_url == "https://example.test/gobekli"
    assert (from_pin.model_extra or {})["subject_slug"] == "gobekli"


def test_distribution_writes_only_publish_status(tmp_path: Path) -> None:
    item = new_content_item(
        topic="Kept",
        final_caption="Body stays.",
        video_path="reel.mp4",
        channel_id="ancient_knowledge",
        post_type="REEL",
        title="Kept title",
    )
    path = tmp_path / "content_library.json"
    path.write_text(json.dumps([item.to_library_row()]), encoding="utf-8")
    updated = update_publish_status(
        path,
        platform="youtube",
        status="posted",
        video_path="reel.mp4",
    )
    assert updated["posting_status"]["youtube"] == "posted"
    assert updated["posting_status"]["pinterest"] == "pending"
    assert updated["topic"] == "Kept"
    assert updated["final_caption"] == "Body stays."
    assert updated["base_metadata"]["title"] == "Kept title"


def test_upgrade_backs_up_and_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "content_library.json"
    original = {
        "topic": "Gobekli",
        "final_caption": "Stones.",
        "video_path": "a.mp4",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "subject_slug": "gobekli",
        "variant_index": 2,
        "youtube_video_id": "abc123",
        "facebook_caption": "Custom facebook line",
    }
    path.write_text(json.dumps([original]), encoding="utf-8")
    assert upgrade_content_library(path, channel_id="ancient_knowledge") == 1
    backup = path.with_name("content_library.json.bak")
    assert json.loads(backup.read_text(encoding="utf-8")) == [original]
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved[0]["schema_version"] == 1
    assert saved[0]["subject_slug"] == "gobekli"
    assert saved[0]["variant_index"] == 2
    assert saved[0]["youtube_video_id"] == "abc123"
    assert saved[0]["final_caption"] == "Stones."
    assert saved[0]["facebook_caption"] == "Custom facebook line"
    assert saved[0]["base_metadata"]["caption"]
    assert upgrade_content_library(path, channel_id="ancient_knowledge") == 0
    assert backup.read_text(encoding="utf-8") == json.dumps([original])
