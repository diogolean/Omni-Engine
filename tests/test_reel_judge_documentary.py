# -*- coding: utf-8 -*-
"""The smoke-5 stills must fail. Descriptions stand in for the pictures."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from modules.reel_qa.documentary import documentary_failures
from modules.reel_qa.relevance import evaluate_chunk_relevance

# Independent review of the two reels the lenient judge scored 1.00 and 0.95.
_FAILURES = (
    (
        "mainstream narratives struggle with these techniques",
        "Puma Punku",
        "a modern library with bookshelves and a reading lamp",
        "anachronism",
    ),
    (
        "the stones were fitted with extraordinary care",
        "Puma Punku",
        "The ruins look CGI or miniature-clay, not photoreal",
        "non_photoreal",
    ),
    (
        "ancient technological perception",
        "Puma Punku",
        "a steampunk gears background",
        "anachronism",
    ),
    (
        "pre-Columbian knowledge",
        "Quimbaya gold pendants",
        "a futuristic gold fighter jet in a sci-fi hangar",
        "anachronism",
    ),
    (
        "experimental replicas",
        "Quimbaya gold pendants",
        "gold planes floating in a spaceship corridor",
        "anachronism",
    ),
    (
        "historians debating",
        "Quimbaya gold pendants",
        "a gold airplane embedded in a bald man's head",
        "anatomy",
    ),
    (
        "historians debating",
        "Quimbaya gold pendants",
        "a mangled hand merging into a pen",
        "anatomy",
    ),
)


def test_smoke5_descriptions_fail_the_documentary_standard() -> None:
    for spoken, _subject, description, label in _FAILURES:
        defects = documentary_failures(description, spoken=spoken)
        assert label in defects, (description, defects)
        assert defects, description


def test_a_photoreal_museum_still_is_not_flagged() -> None:
    description = (
        "A photoreal documentary photograph of small gold pendants "
        "in a glass museum case, natural metal and dust."
    )
    assert documentary_failures(
        description, spoken="historians debating the Quimbaya pendants",
    ) == []
    gears = (
        "A photoreal close photograph of corroded bronze gear fragments "
        "in a museum drawer."
    )
    assert documentary_failures(gears, spoken="The gears still surprise researchers.") == []


def test_a_perfect_score_still_fails_when_the_description_is_a_library(
    tmp_path: Path, monkeypatch,
) -> None:
    img = tmp_path / "still.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 32)
    captured: dict[str, str] = {}

    class _Provider:
        def evaluate(self, prompt, paths, labels=None):
            del paths, labels
            captured["prompt"] = prompt
            return SimpleNamespace(
                text=(
                    '{"is_relevant": true, "relevance_score": 1.0, '
                    '"shows_expected_subject": true, '
                    '"image_description": "a modern library with bookshelves '
                    'and a reading lamp", '
                    '"detected_subjects": ["Puma Punku"], '
                    '"rejection_reason": null}'
                ),
                provider="openrouter",
                model_id="qwen",
            )

    monkeypatch.setattr("modules.reel_qa.relevance._provider", lambda name: _Provider())
    verdict = evaluate_chunk_relevance(
        img,
        "mainstream narratives struggle with these techniques",
        "EXPECTED SUBJECT: Puma Punku. andesite H-blocks.",
    )
    assert verdict["is_relevant"] is False
    assert verdict["hard_reject"] is True
    assert verdict["publishable"] is False
    assert verdict["relevance_score"] < 0.70
    assert "anachronism" in str(verdict["rejection_reason"])
    prompt = captured["prompt"]
    assert "0.70" in prompt
    assert "IMAGE" in prompt
    assert "anachronism" in prompt
    assert "non_photoreal" in prompt or "non-photoreal" in prompt.lower() or "CGI" in prompt
    assert "anatomy" in prompt.lower()


def test_the_qwen_object_prompt_keeps_a_labelled_museum_jar(
    tmp_path: Path, monkeypatch,
) -> None:
    img = tmp_path / "jar.jpg"
    img.write_bytes(b"\xff\xd8\xff" + b"x" * 32)
    captured: dict[str, str] = {}

    class _Provider:
        def evaluate(self, prompt, paths, labels=None):
            del paths, labels
            captured["prompt"] = prompt
            return SimpleNamespace(
                text=(
                    '{"is_relevant": false, "relevance_score": 0.0, '
                    '"shows_expected_subject": true, '
                    '"image_description": "photograph of a clay jar in a museum '
                    'case with a small label and a scale bar", '
                    '"non_photoreal": true, "contains_text": true, '
                    '"setting_contradicts_beat": true, "anachronism": true, '
                    '"anatomy_defect": false, '
                    '"detected_subjects": ["clay jar"], '
                    '"rejection_reason": "museum label"}'
                ),
                provider="openrouter",
                model_id="qwen/qwen3.5-flash-02-23",
            )

    monkeypatch.setattr("modules.reel_qa.relevance._provider", lambda name: _Provider())
    verdict = evaluate_chunk_relevance(
        img,
        "PartherJar InnerSystem",
        "PartherJar InnerSystem",
        providers=["qwen"],
        score_against="narration",
    )
    prompt = captured["prompt"].lower()
    assert "museum" in prompt
    assert "scale bar" in prompt
    assert "do not set setting_contradicts_beat" in prompt
    assert "plain background" in prompt
    assert verdict["is_relevant"] is True
    assert verdict["relevance_score"] >= 0.70
    assert verdict["contains_text"] is False
    assert verdict["non_photoreal"] is False
    assert "setting_contradicts" not in str(verdict["rejection_reason"])

    captured.clear()

    class _Site:
        def evaluate(self, prompt, paths, labels=None):
            del paths, labels
            captured["prompt"] = prompt
            return SimpleNamespace(
                text=(
                    '{"is_relevant": false, "relevance_score": 0.2, '
                    '"image_description": "a museum label filling the frame", '
                    '"contains_text": true, "rejection_reason": "label"}'
                ),
                provider="openrouter",
                model_id="qwen/qwen3.5-flash-02-23",
            )

    monkeypatch.setattr("modules.reel_qa.relevance._provider", lambda name: _Site())
    site = evaluate_chunk_relevance(
        img,
        "limestone enclosure",
        "limestone enclosure",
        providers=["qwen"],
        score_against="site",
    )
    site_prompt = captured["prompt"].lower()
    assert "do not set setting_contradicts_beat" not in site_prompt
    assert "museum label" in site_prompt
    assert site["is_relevant"] is False
