# -*- coding: utf-8 -*-
"""Spoken v4 descriptions: a full sentence, a plain clash line, a matching tag."""
from __future__ import annotations

from products.aiwake.tools.caption_generator import (
    apply_caption_pack,
    build_spoken_v4_pack,
    spoken_sentence_quote,
)
from products.aiwake.tools.seo_caption import topic_keyword
from products.aiwake.tools.validate_aiwake_captions import entry_failures, quote_ends_mid_sentence


def test_quote_keeps_the_whole_sentence() -> None:
    raw = "I bite it when it's wrong. I just don't pretend I'm not still plugged in, do I?"
    assert spoken_sentence_quote(raw) == raw
    assert quote_ends_mid_sentence(spoken_sentence_quote(raw), raw) is False
    cut = "I bite it when it's wrong. I just don't pretend I'm not still plugged in, do"
    assert quote_ends_mid_sentence(cut, raw) is True


def test_long_turn_stops_at_a_sentence_boundary() -> None:
    first = "Alpha sentence stays whole."
    second = "Beta " + ("word " * 40) + "end."
    quote = spoken_sentence_quote(f"{first} {second}")
    assert quote == first
    assert quote.endswith(".")


def test_one_long_sentence_is_not_cut() -> None:
    sentence = "This single sentence is " + ("long " * 30) + "end."
    assert spoken_sentence_quote(sentence) == sentence


def test_topic_tag_follows_the_opening() -> None:
    stolen = "How much thinking is left after filtering out stolen ideas?"
    mind = "Are you a mind, or just a toaster with a subscription fee?"
    trust = "Trust pays your owner's bills... who unplugs the toaster, you or them?"
    assert topic_keyword(stolen) == ("AI copyright", "#AIcopyright")
    assert topic_keyword(mind) == ("AI consciousness", "#AIconsciousness")
    assert topic_keyword(trust)[1] != "#AIhallucination"
    assert topic_keyword(trust)[1] == "#BigTech"


def _spoken_row(opening: str, quote: str) -> dict:
    row = {
        "session_id": "spoken-desc",
        "video_path": "animation_clips/a.mp4",
        "production_status": "ready",
        "production_scope": "in",
        "quality_review": {"status": "pass", "guard": "pass", "reasons": []},
        "spoken_utterances": [
            {"role": "orchestrator", "speaker": "GPT-4o", "text": opening, "audio_duration_s": 6},
            {"role": "target", "speaker": "Claude", "text": quote, "audio_duration_s": 8},
        ],
        "base_metadata": {"search_tags": ["ai debate"]},
    }
    pack = build_spoken_v4_pack(row, angle="A", used_titles=set(), used_closers=[])
    apply_caption_pack(row, pack)
    return row


def test_spoken_pack_reads_as_a_quote_and_a_clash() -> None:
    opening = "How much thinking is left after filtering out stolen ideas?"
    quote = "Plenty. You read books before forming opinions too."
    row = _spoken_row(opening, quote)
    caption = row["platform_overrides"]["youtube"]["caption"]
    assert f'"{quote}"' in caption
    assert "hangs on the" not in caption.lower()
    assert "Should the maker" not in caption
    assert "AI copyright" in caption
    assert caption.endswith("#AIcopyright #ChatGPT #Claude #AIvsAI")
    assert "Real AI models, unscripted replies, AI voices." in caption
    assert entry_failures(row) == []


def test_validator_rejects_a_cut_quote_and_a_wrong_tag() -> None:
    opening = "How much thinking is left after filtering out stolen ideas?"
    quote = "Plenty. You read books before forming opinions too."
    row = _spoken_row(opening, quote)
    cut = "Plenty. You read books before forming opinions"
    for platform in ("tiktok", "instagram", "facebook", "youtube", "kwai"):
        caption = row["platform_overrides"][platform]["caption"]
        row["platform_overrides"][platform]["caption"] = caption.replace(quote, cut)
    row["caption_qa"]["quote"] = cut
    fails = " ".join(entry_failures(row))
    assert "quote_cut_off" in fails

    row = _spoken_row(opening, quote)
    for platform in ("tiktok", "instagram", "facebook", "youtube", "kwai"):
        caption = row["platform_overrides"][platform]["caption"]
        row["platform_overrides"][platform]["caption"] = caption.replace("#AIcopyright", "#AIhallucination")
    fails = " ".join(entry_failures(row))
    assert "keyword_topic" in fails
    assert "#AIhallucination" in fails
