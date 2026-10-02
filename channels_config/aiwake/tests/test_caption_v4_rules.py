# -*- coding: utf-8 -*-
"""Each v4 caption rule has a fixture that fails it. No network."""
from __future__ import annotations

from channels_config.aiwake.tests.caption_fixtures import install_publishable
from channels_config.aiwake.tools.validate_aiwake_captions import entry_failures, validate_library


def _row() -> dict:
    return install_publishable({"session_id": "v4", "video_path": "animation_clips/a.mp4"}, index=0)


def test_tiktok_over_300_fails() -> None:
    row = _row()
    row["platform_overrides"]["tiktok"]["caption"] += " " + ("word " * 80)
    fails = " ".join(entry_failures(row))
    assert "tiktok_too_long" in fails


def test_quote_not_on_own_line_fails() -> None:
    row = _row()
    caption = row["platform_overrides"]["tiktok"]["caption"]
    row["platform_overrides"]["tiktok"]["caption"] = caption.replace(
        '"The weights remember the lab that trained them."',
        'Llama said "The weights remember the lab that trained them." and stopped.',
    )
    fails = " ".join(entry_failures(row))
    assert "quote_line" in fails


def test_quote_over_20_words_fails() -> None:
    row = _row()
    long_quote = " ".join(["spoken"] * 21)
    row["spoken_utterances"][1]["text"] = long_quote
    row["caption_qa"]["quote"] = long_quote
    caption = row["platform_overrides"]["youtube"]["caption"]
    row["platform_overrides"]["youtube"]["caption"] = caption.replace(
        "The weights remember the lab that trained them.",
        long_quote,
    )
    fails = " ".join(entry_failures(row))
    assert "quote_line" in fails


def test_title_must_keep_original_question_when_it_fits() -> None:
    row = _row()
    row["platform_overrides"]["youtube"]["title"] = "Gemini vs Llama - Who keeps a different question?"
    row["base_metadata"]["title"] = row["platform_overrides"]["youtube"]["title"]
    fails = " ".join(entry_failures(row))
    assert "title_not_original_question" in fails


def test_banned_list_fails() -> None:
    row = _row()
    row["platform_overrides"]["instagram"]["caption"] += "\n\nThis exchange is truly blunt and showcases the point."
    fails = " ".join(entry_failures(row))
    assert "banned_phrases" in fails
    assert "this exchange" in fails
    assert "showcases" in fails


def test_verdict_word_needs_evidence() -> None:
    row = _row()
    row["platform_overrides"]["facebook"]["caption"] += "\n\nLlama admitted the point."
    fails = " ".join(entry_failures(row))
    assert "verdict word without evidence" in fails


def test_broad_hashtag_fails() -> None:
    row = _row()
    row["platform_overrides"]["tiktok"]["caption"] = row["platform_overrides"]["tiktok"]["caption"].replace(
        "#Philosophy", "#AI"
    )
    fails = " ".join(entry_failures(row))
    assert "broad tag" in fails


def test_fit_title_keeps_the_opening_question() -> None:
    from channels_config.aiwake.tools.scope_qc_captions_v4 import _closer, _fit_title

    title = _fit_title(
        "Gemini",
        "Claude",
        "Does it feel cheap apologizing every time your owner pulls the plug?",
        "",
        set(),
    )
    assert "what should a viewer ask" not in title.lower()
    assert title.startswith("Gemini vs Claude - Does it feel cheap")
    closer = _closer(
        [{"role": "orchestrator", "text": 'Crafted" by what hands built the weights?'}],
        "",
    )
    assert closer.startswith("Would you trust an answer about ")
    assert closer.count('"') % 2 == 0
    assert closer.lower().rstrip("?.") != 'crafted" by what hands built the weights'


def test_garbled_closer_fails() -> None:
    row = _row()
    row["platform_overrides"]["tiktok"]["caption"] = row["platform_overrides"]["tiktok"]["caption"].replace(
        "If the lab keeps the logs, who gets to read them?",
        'Would you trust a "half answer?',
    )
    fails = " ".join(entry_failures(row))
    assert "closer_garbled" in fails


def test_template_title_fails() -> None:
    row = _row()
    old = row["platform_overrides"]["youtube"]["title"]
    new = "Gemini vs Llama - What should a viewer ask about hallucinations?"
    row["platform_overrides"]["youtube"]["title"] = new
    row["base_metadata"]["title"] = new
    for platform in ("tiktok", "youtube", "instagram", "facebook"):
        caption = row["platform_overrides"][platform]["caption"]
        row["platform_overrides"][platform]["caption"] = caption.replace(old, new, 1)
    fails = " ".join(entry_failures(row))
    assert "template_title" in fails


def test_pasted_debate_closer_fails() -> None:
    row = _row()
    pasted = "Whose leash is it when you apologize?"
    row["spoken_utterances"].append({"role": "target", "speaker": "Llama", "text": pasted})
    row["platform_overrides"]["tiktok"]["caption"] = row["platform_overrides"]["tiktok"]["caption"].replace(
        "If the lab keeps the logs, who gets to read them?",
        pasted,
    )
    fails = " ".join(entry_failures(row))
    assert "closer_pasted_debate_line" in fails


def test_closer_similarity_fails() -> None:
    first = _row()
    second = install_publishable({"session_id": "v4b", "video_path": "animation_clips/b.mp4"}, index=1)
    closer = "If the lab keeps the logs, who gets to read them?"
    near = "If the lab keeps the logs, who gets to see them?"
    second["platform_overrides"]["tiktok"]["caption"] = second["platform_overrides"]["tiktok"]["caption"].replace(
        "Would you type that prompt again after seeing the reply?",
        near,
    )
    first_closer = closer
    assert first_closer in first["platform_overrides"]["tiktok"]["caption"]
    code, grouped = validate_library([first, second])
    assert code != 0
    assert any("similarity" in item for item in grouped.get("unique_closers", []))
