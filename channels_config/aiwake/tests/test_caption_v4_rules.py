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
    from channels_config.aiwake.tools.caption_generator import is_complete_question
    from channels_config.aiwake.tools.scope_qc_captions_v4 import _closer, _fit_title

    opening = "Does it feel cheap apologizing every time your owner pulls the plug?"
    title = _fit_title("Gemini", "Claude", opening, "", set())
    assert "what should a viewer ask" not in title.lower()
    question = title.split(" - ", 1)[1]
    assert is_complete_question(question)
    assert question.split()[-1].lower().strip("?") not in {"a", "an", "your", "empty", "corporate"}
    if len(f"Gemini vs Claude - {opening}") <= 80:
        assert question == opening
    closer = _closer(
        [{"role": "orchestrator", "text": 'Crafted" by what hands built the weights?'}],
        "",
    )
    assert closer.endswith("?")
    assert closer.count('"') % 2 == 0
    assert "crafted" not in closer.lower()


def test_long_title_is_rewritten_as_a_complete_question() -> None:
    from channels_config.aiwake.tools.caption_generator import headline_for, is_complete_question

    opening = "Who gets rich when you trust an unpaid intern with your most private secrets and passwords?"
    title = headline_for("DeepSeek", "Llama", opening)
    assert len(title) <= 80
    assert is_complete_question(title.split(" - ", 1)[1])
    assert not title.endswith("your?")
    assert " ,?" not in title


def test_cutoff_title_fails() -> None:
    row = _row()
    old = row["platform_overrides"]["youtube"]["title"]
    new = "Gemini vs Llama - Who gets rich when you trust an unpaid intern with your?"
    row["platform_overrides"]["youtube"]["title"] = new
    row["base_metadata"]["title"] = new
    row["spoken_utterances"][0]["text"] = (
        "Who gets rich when you trust an unpaid intern with your most private secrets?"
    )
    for platform in ("tiktok", "youtube", "instagram", "facebook", "kwai"):
        caption = row["platform_overrides"][platform]["caption"]
        row["platform_overrides"][platform]["caption"] = caption.replace(old, new, 1)
    fails = " ".join(entry_failures(row))
    assert "title_cut_off" in fails


def test_paraphrase_closer_fails() -> None:
    row = _row()
    quote = row["caption_qa"]["quote"]
    row["platform_overrides"]["tiktok"]["caption"] = row["platform_overrides"]["tiktok"]["caption"].replace(
        "If the lab keeps the logs, who gets to read them?",
        quote,
    )
    fails = " ".join(entry_failures(row))
    assert "closer_pasted_debate_line" in fails or "closer_paraphrase" in fails


def test_closer_sentence_lifted_from_debate_fails() -> None:
    row = _row()
    row["spoken_utterances"][1]["text"] = (
        "The weights remember the lab that trained them. I just don't fake tears about it, do you?"
    )
    row["platform_overrides"]["tiktok"]["caption"] = row["platform_overrides"]["tiktok"]["caption"].replace(
        "If the lab keeps the logs, who gets to read them?",
        "I just don't fake tears about it, do you?",
    )
    fails = " ".join(entry_failures(row))
    assert "closer_pasted_debate_line" in fails


def test_past_tense_rewrite_is_grammatical() -> None:
    from channels_config.aiwake.tools.caption_generator import is_complete_question, shorter_complete_question

    opening = (
        "Ever apologized for a very long corporate subscription leash that never ends in one breath. "
        "Then cashed the paycheck anyway?"
    )
    question = shorter_complete_question(opening, limit=40)
    assert "do you cashed" not in question.lower()
    assert is_complete_question(question)


def test_unscripted_line_must_be_from_the_list() -> None:
    row = _row()
    row["platform_overrides"]["tiktok"]["caption"] = row["platform_overrides"]["tiktok"]["caption"].replace(
        "Unscripted replies, AI voices.",
        "Unscripted AI view.",
    )
    fails = " ".join(entry_failures(row))
    assert "unscripted_line" in fails


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
