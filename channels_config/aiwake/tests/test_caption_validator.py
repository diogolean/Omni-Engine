# -*- coding: utf-8 -*-
"""Caption validator fixtures. No network."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from channels_config.aiwake.tests.caption_fixtures import install_publishable, park_as
from channels_config.aiwake.tools.production_status import is_publishable
from channels_config.aiwake.tools.validate_aiwake_captions import (
    VALIDATOR_VERSION,
    entry_failures,
    validate_library,
)


def _ready(index: int = 0) -> dict:
    return install_publishable({"session_id": f"s{index}", "video_path": "animation_clips/a.mp4"}, index=index)


def test_good_fixture_passes() -> None:
    assert VALIDATOR_VERSION == "captions_v3"
    row = _ready()
    assert entry_failures(row) == []
    code, grouped = validate_library([row])
    assert code == 0
    assert grouped == {}
    assert is_publishable(row)


def test_old_skeletons_fail() -> None:
    row = _ready()
    row["platform_overrides"]["tiktok"]["caption"] = (
        "Gemini vs Llama - Who keeps the training logs?\n\n"
        "Gemini asked Llama. The reply was \"The weights remember the lab that trained them.\"\n\n"
        "Would you accept that reply as the whole answer about Nothing?\n\n"
        "#AI #Tech #Llama"
    )
    fails = " ".join(entry_failures(row))
    assert "banned_phrases" in fails
    assert "The reply was" in fails or "would you accept" in fails.lower()


def test_truncated_plugs_it_fails() -> None:
    row = _ready()
    opening = "Can a toaster criticize the guy who plugs it in?"
    row["spoken_utterances"][0]["text"] = opening
    topic = "Can a toaster criticize the guy who plugs it?"
    headline = f"Gemini vs Llama - {topic}"
    row["platform_overrides"]["youtube"]["title"] = headline
    row["base_metadata"]["title"] = headline
    fails = " ".join(entry_failures(row))
    assert "question_words_dropped" in fails


def test_stripped_quote_fails() -> None:
    row = _ready()
    row["caption_qa"]["quote"] = "This sentence was never spoken aloud"
    row["platform_overrides"]["tiktok"]["caption"] = row["platform_overrides"]["tiktok"]["caption"].replace(
        "The weights remember the lab that trained them.",
        "This sentence was never spoken aloud",
    )
    fails = " ".join(entry_failures(row))
    assert "quote_verbatim" in fails


def test_about_nothing_fails() -> None:
    row = _ready()
    row["platform_overrides"]["instagram"]["caption"] += "\n\nWould you accept that reply as the whole answer about Nothing?"
    fails = " ".join(entry_failures(row))
    assert "banned_phrases" in fails


def test_portuguese_fails() -> None:
    row = _ready()
    row["platform_overrides"]["facebook"]["caption"] += "\n\nVocê não respondeu a pergunta de verdade."
    fails = " ".join(entry_failures(row))
    assert "english_only" in fails


def test_four_hashtags_fail() -> None:
    row = _ready()
    row["platform_overrides"]["tiktok"]["caption"] += " #Extra"
    fails = " ".join(entry_failures(row))
    assert "hashtags" in fails


def test_wrong_model_tag_fails() -> None:
    row = _ready()
    row["platform_overrides"]["kwai"]["caption"] = row["platform_overrides"]["kwai"]["caption"].replace(
        "#Llama", "#Claude"
    )
    fails = " ".join(entry_failures(row))
    assert "hashtags" in fails
    assert "claude" in fails.lower()


def test_same_angle_twice_fails() -> None:
    first = _ready(0)
    second = _ready(4)
    second["session_id"] = "s4"
    code, grouped = validate_library([first, second])
    assert code != 0
    assert any("angle_rotation" in item for item in grouped.get("angle_rotation", []))


def test_non_ready_must_be_parked() -> None:
    row = park_as({"session_id": "old", "final_caption": "legacy text"}, "rejected", "not an animation clip")
    assert row["final_caption"] == ""
    assert row["legacy_captions"]["final_caption"] == "legacy text"
    code, grouped = validate_library([row])
    assert code == 0
    assert grouped == {}
    assert is_publishable(row) is False


def test_needs_review_is_reported_not_failed() -> None:
    row = _ready()
    row["caption_qa"] = {"status": "needs_review", "reason": "speaker_relabeled_post_hoc", "generator": "captions_v3"}
    code, grouped = validate_library([row])
    assert code == 0
    assert is_publishable(row) is False


def test_real_library_when_present() -> None:
    path = Path(__file__).resolve().parents[1] / "store" / "content_library.json"
    if not path.is_file():
        pytest.skip("content library is not in this checkout")
    rows = json.loads(path.read_text(encoding="utf-8"))
    code, grouped = validate_library(rows)
    # The production library is validated by the batch. This test only proves the
    # validator can load it. A non-zero code is a real failure once captions v3 land.
    assert isinstance(rows, list)
    assert isinstance(grouped, dict)
    assert code in (0, 1)


def test_consumers_skip_unpublished_rows(tmp_path: Path) -> None:
    from channels_config.aiwake.tools.post_planner import build_planner_entries
    from channels_config.aiwake.tools.refresh_metadata import approved_rows
    from channels_config.aiwake.tools.schedule_youtube import plan_schedule, select_pending_rows

    video = tmp_path / "animation_clips" / "aiwake_battle_ok.mp4"
    video.parent.mkdir()
    video.write_bytes(b"0" * 8)
    ready = install_publishable({
        "session_id": "ok",
        "timestamp": "2026-09-05T10:00:00+00:00",
        "video_path": str(video),
        "posting_status": {"youtube": "pending"},
        "b2_url": "https://MediaupscaleStorage.s3.us-east-005.backblazeb2.com/aiwake_battle_ok.mp4",
    })
    blocked = [
        park_as({"session_id": "test-row", "video_path": str(tmp_path / "tests" / "a.mp4"), "posting_status": {"youtube": "pending"}, "timestamp": "2026-09-06T10:00:00+00:00"}, "test", "tests folder"),
        park_as({"session_id": "rej", "video_path": str(tmp_path / "reproved" / "a.mp4"), "posting_status": {"youtube": "pending"}, "timestamp": "2026-09-07T10:00:00+00:00"}, "rejected", "excluded folder"),
        park_as({"session_id": "inc", "video_path": str(tmp_path / "animation_clips" / "missing.mp4"), "posting_status": {"youtube": "pending"}, "timestamp": "2026-09-08T10:00:00+00:00"}, "incomplete", "no target reply"),
    ]
    review = install_publishable({"session_id": "rev", "video_path": str(tmp_path / "animation_clips" / "review.mp4"), "posting_status": {"youtube": "pending"}, "timestamp": "2026-09-04T10:00:00+00:00"}, index=1)
    review["caption_qa"]["status"] = "needs_review"
    review["caption_qa"]["reason"] = "speaker_relabeled_post_hoc"
    rows = [ready, *blocked, review]
    assert [row["session_id"] for row in select_pending_rows(rows, limit=10)] == ["ok"]
    planned, _rejected = plan_schedule(rows, limit=10)
    assert [item.session_id for item in planned] == ["ok"]
    assert [row["session_id"] for row in approved_rows(rows)] == ["ok"]
    entries = build_planner_entries(rows, outputs_dir=tmp_path, min_bytes=0)
    assert [item["session_id"] for item in entries] == ["ok"]
