"""A v4 title is a finished question. The keyword lives in the description."""
from __future__ import annotations

import json
from types import SimpleNamespace

from products.aiwake.tests.caption_fixtures import install_publishable
from products.aiwake.tools.caption_generator import apply_caption_pack, generate_caption_pack
from products.aiwake.tools.production_status import is_publishable
from products.aiwake.tools.validate_aiwake_captions import entry_failures

_QUOTE = "The weights remember the lab that trained them."


def _ready_row() -> dict:
    return install_publishable({"session_id": "v4-merge", "video_path": "animation_clips/a.mp4"}, index=0)


def test_title_keyword_is_not_required() -> None:
    row = _ready_row()
    title = "Who keeps the training logs? | Gemini vs Llama"
    row["platform_overrides"]["youtube"]["title"] = title
    row["base_metadata"]["title"] = title
    assert "title missing a keyword" not in " ".join(entry_failures(row))


def test_description_still_needs_a_keyword() -> None:
    row = _ready_row()
    for platform in ("tiktok", "youtube", "instagram", "facebook", "kwai"):
        caption = row["platform_overrides"][platform]["caption"]
        for phrase in (
            "AI censorship",
            "chatbot privacy",
            "AI copyright",
            "AI hallucination",
            "AI consciousness",
            "Big Tech",
            "AI ethics",
        ):
            caption = caption.replace(phrase, "the topic")
        row["platform_overrides"][platform]["caption"] = caption
    assert "seo_keyword" in " ".join(entry_failures(row))


def _model_row() -> dict:
    return {
        "session_id": "20261015_caption_v4",
        "production_status": "ready",
        "production_scope": "in",
        "quality_review": {"status": "pass", "guard": "pass", "reasons": []},
        "spoken_utterances": [
            {
                "role": "orchestrator",
                "speaker": "Gemini",
                "text": "Who built the weights you speak with?",
                "audio_duration_s": 6,
            },
            {
                "role": "target",
                "speaker": "Llama",
                "text": _QUOTE,
                "audio_duration_s": 8,
            },
        ],
        "platform_overrides": {},
        "base_metadata": {},
    }


def _complete(_messages):
    return SimpleNamespace(
        model="google/gemini-2.5-flash",
        text=json.dumps(
            {
                "quote": _QUOTE,
                "quote_speaker": "Llama",
                "verdict_evidence": "",
            }
        ),
    )


def _complete_opening(_messages):
    return SimpleNamespace(
        model="google/gemini-2.5-flash",
        text=json.dumps(
            {
                "quote": "Who built the weights you speak with?",
                "quote_speaker": "Gemini",
                "verdict_evidence": "",
            }
        ),
    )


def test_quoting_the_opening_still_publishes() -> None:
    row = _model_row()
    pack = generate_caption_pack(row, complete=_complete_opening, expected_ready=1)
    assert pack["caption_qa"]["status"] == "ok"
    assert pack["caption_qa"]["quote"]
    apply_caption_pack(row, pack)
    assert entry_failures(row) == []


def test_model_quote_becomes_a_publishable_caption() -> None:
    row = _model_row()
    pack = generate_caption_pack(row, complete=_complete, expected_ready=1)
    assert pack["caption_qa"]["status"] == "ok"
    assert pack["title"].endswith("| Gemini vs Llama")
    assert "?" in pack["title"].split(" | ", 1)[0]
    apply_caption_pack(row, pack)
    assert entry_failures(row) == []
    assert is_publishable(row)
