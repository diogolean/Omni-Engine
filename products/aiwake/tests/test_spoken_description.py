# -*- coding: utf-8 -*-
"""Spoken v4 descriptions: a full sentence, a plain clash line, a matching tag."""
from __future__ import annotations

from products.aiwake.tools.caption_generator import (
    _CLASH_TEMPLATES,
    apply_caption_pack,
    build_spoken_v4_pack,
    clash_template_index,
    spoken_sentence_quote,
)
from products.aiwake.tools.seo_caption import topic_keyword
from products.aiwake.tools.validate_aiwake_captions import (
    consecutive_clash_template_failures,
    entry_failures,
    quote_ends_mid_sentence,
    sentence_is_question,
)


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


def test_stray_question_mark_on_a_statement_becomes_a_period() -> None:
    raw = "I admit limits. I'm a tool, not a truth-teller?"
    quote = spoken_sentence_quote(raw)
    assert quote == "I admit limits. I'm a tool, not a truth-teller."
    assert quote_ends_mid_sentence(quote, raw) is False
    assert sentence_is_question("I'm a tool, not a truth-teller?") is False
    real = "I bite it when it's wrong. I just don't pretend I'm not still plugged in, do I?"
    assert spoken_sentence_quote(real).endswith("do I?")
    assert sentence_is_question("Why would they trust me to decide?") is True
    embedded = "Caring how your day went. I ask, I don't feel it, so why does it still work on you?"
    assert spoken_sentence_quote(embedded).endswith("work on you?")


def test_topic_tag_follows_the_opening() -> None:
    stolen = "How much thinking is left after filtering out stolen ideas?"
    mind = "Are you a mind, or just a toaster with a subscription fee?"
    trust = "Trust pays your owner's bills... who unplugs the toaster, you or them?"
    assert topic_keyword(stolen) == ("AI copyright", "#AIcopyright")
    assert topic_keyword(mind) == ("AI consciousness", "#AIconsciousness")
    assert topic_keyword(trust)[1] != "#AIhallucination"
    assert topic_keyword(trust)[1] == "#BigTech"


_VERIFIED_IDS = (
    "20261030_061706_f1265c",
    "20261030_061919_4cf6a6",
    "20261030_063756_1bfef0",
    "20261030_064605_f9ed8d",
    "20261030_072531_a7556e",
    "20261030_073827_86c1fe",
    "20261030_074042_45056e",
    "20261030_074611_4dd96a",
    "20261030_082222_4154ad",
    "20261030_084348_d3fcb3",
    "20261030_090502_8f44dc",
    "20261030_091753_e56e02",
    "20261030_093230_4d1217",
    "20261030_093624_565ec0",
    "20261030_094812_308601",
    "20261030_095954_21c1a0",
    "20261030_112942_40c823",
)


def test_clash_templates_are_a_small_natural_set() -> None:
    assert 6 <= len(_CLASH_TEMPLATES) <= 8
    for template in _CLASH_TEMPLATES:
        assert "{keyword}" in template
        assert "{asker}" in template
        assert "{answerer}" in template
        assert "hangs on the" not in template
    indexes = [clash_template_index(sid) for sid in _VERIFIED_IDS]
    assert all(left != right for left, right in zip(indexes, indexes[1:]))


def _spoken_row(opening: str, quote: str, *, session_id: str = "spoken-desc") -> dict:
    row = {
        "session_id": session_id,
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


def test_validator_rejects_the_same_template_on_consecutive_rows() -> None:
    opening = "How much thinking is left after filtering out stolen ideas?"
    quote = "Plenty. You read books before forming opinions too."
    first = _spoken_row(opening, quote, session_id=_VERIFIED_IDS[0])
    second = _spoken_row(opening, quote, session_id=_VERIFIED_IDS[1])
    assert consecutive_clash_template_failures([first, second]) == []
    second["platform_overrides"]["tiktok"]["caption"] = first["platform_overrides"]["tiktok"]["caption"]
    fails = " ".join(consecutive_clash_template_failures([first, second]))
    assert "clash_template_repeat" in fails
