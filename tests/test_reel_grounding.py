# -*- coding: utf-8 -*-
"""Object-first reel prompts, the QA hit rate, and the still cache."""
from __future__ import annotations

from pathlib import Path

import pytest

from production.formats.economic_reel.grounding.memory import recall, remember
from production.formats.economic_reel.grounding.prompt import assemble_beat, ground_image_prompt
from production.formats.economic_reel.grounding.still_cache import cache_matches_spoken, write_spoken_sidecar
from production.qa.metrics import first_pass_summary
from production.qa.prompts import extract_site, refine_prompt

_SCRIPT = (
    "An iron hammer with a wooden handle was found encased in rock near "
    "London, Texas. The wood preservation of the handle is the puzzle. "
    "Metallurgical analysis of the iron head followed. Historians debate the age."
)
_TOPIC = "The London Hammer"
_RUIN_DRAFT = (
    "SUBJECT — WIDE LANDSCAPE / RUINS: A broad establishing landscape of "
    "The London Hammer — horizon visible, torchlit stone ruins and fire."
)


def _ground(spoken: str, **kwargs: object) -> str:
    return ground_image_prompt(
        topic=_TOPIC,
        script=_SCRIPT,
        spoken=spoken,
        draft=_RUIN_DRAFT,
        shot_text="Wide establishing shot — horizon visible, landscape dominating.",
        lighting_text="Torch / firelight lit from BELOW the subject.",
        channel="ancient_knowledge",
        **kwargs,  # type: ignore[arg-type]
    )


def test_london_find_spot_does_not_lead_as_a_city() -> None:
    prompt = _ground("This artefact from London, Texas should not exist.")
    low = prompt.lower()
    assert low.startswith("primary subject:")
    assert "hammer" in low
    head = low.split("place is context only")[0]
    assert "urban alley" not in head
    assert "wide landscape" not in head
    assert "london, texas" in low
    assert "famous city of london" in low
    assert low.find("hammer") < low.find("london, texas")


def test_wood_handle_is_a_close_up_not_stone_ruins() -> None:
    prompt = _ground("The wood preservation of the handle still puzzles the lab.")
    low = prompt.lower()
    assert "wood" in low and "handle" in low
    assert "close-up" in low or "close up" in low
    head = low.split("do not depict")[0]
    assert "wide landscape / ruins" not in head
    assert "torchlit ruins" not in head


def test_metallurgy_stays_on_the_artefact() -> None:
    prompt = _ground("Metallurgical analysis of the metal shows an unusual composition.")
    low = prompt.lower()
    assert "laboratory" in low or "lab bench" in low or "metal" in low
    assert "hammer" in low
    head = low.split("do not depict")[0]
    assert "fantasy landscape" not in head
    assert "wide landscape / ruins" not in head


def test_abstract_debate_still_shows_the_hammer() -> None:
    prompt = _ground("Historians debate whether this rewrites the timeline.")
    low = prompt.lower()
    assert "hammer" in low
    assert "research table" in low
    assert "debate hall" not in low


def test_extract_site_keeps_the_region() -> None:
    assert extract_site("This artefact from London, Texas should not exist.") == (
        "London, Texas"
    )
    assert extract_site("Off Antikythera, divers found bronze gears.") == "Antikythera"


def test_retry_prompt_puts_the_object_before_the_place() -> None:
    text = refine_prompt(
        "a city",
        "This artefact from London, Texas should not exist.",
        site="London, Texas",
        episode_subject=_TOPIC,
        rejection_reason="judge saw a generic urban alley",
    )
    low = text.lower()
    assert text.index("PRIMARY SUBJECT") < text.index("London, Texas")
    assert "narration sentence:" in low
    assert "context only" in low
    assert "urban alley" in low
    assert "hammer" in low


def test_memory_names_a_previous_rejection(tmp_path: Path) -> None:
    remember(
        channel="ancient_knowledge",
        topic=_TOPIC,
        spoken="This artefact from London, Texas should not exist.",
        visual_subject="the iron hammer in its rock",
        passed=False,
        score=0.2,
        rejection="generic urban alley",
        first_pass=False,
        root=tmp_path,
    )
    hints = recall(
        channel="ancient_knowledge",
        topic=_TOPIC,
        spoken="The hammer sat in Cretaceous rock.",
        root=tmp_path,
    )
    assert any("alley" in item for item in hints.rejected)
    plan = assemble_beat(
        topic=_TOPIC,
        script=_SCRIPT,
        spoken="The hammer sat in Cretaceous rock.",
        channel="ancient_knowledge",
        memory_root=tmp_path,
    )
    assert "generic urban alley" not in plan.positive.lower()
    assert "generic urban alley" in plan.negative.lower()


def test_stale_still_without_a_sidecar_is_not_reused(tmp_path: Path) -> None:
    image = tmp_path / "london_hammer_v01_act02.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 64)
    spoken = "The wood preservation of the handle still puzzles the lab."
    assert cache_matches_spoken(image, spoken) is False
    write_spoken_sidecar(image, spoken, passed=True, score=0.84)
    assert cache_matches_spoken(image, spoken) is True
    assert cache_matches_spoken(image, "A different sentence about a city.") is False


def test_first_pass_hit_rate_ignores_unscored_beats() -> None:
    summary = first_pass_summary(
        [
            {"first_pass": True},
            {"first_pass": True},
            {"first_pass": False},
            {"first_pass": None},
            {},
        ]
    )
    assert summary["first_pass_hits"] == 2
    assert summary["first_pass_total"] == 3
    assert summary["first_pass_hit_rate"] == 2 / 3


def test_reel_free_images_default_off(monkeypatch, tmp_path: Path) -> None:
    from production.providers.image.free.integrate import wrap_image_generate

    monkeypatch.delenv("REEL_FREE_IMAGES", raising=False)
    monkeypatch.delenv("REEL_IMAGE_SOURCE", raising=False)

    def generate(prompt, output_stem=None, output_directory=None, **kwargs):
        del prompt, kwargs
        path = Path(output_directory or tmp_path) / f"{output_stem}.png"
        path.write_bytes(b"png")
        return path

    wrapped = wrap_image_generate(
        generate,
        channel="ancient_knowledge",
        page_cfg={"IMAGE_SOURCE_POLICY": "free_first"},
        producer="reel_image",
    )

    def _boom(**_kwargs):
        raise AssertionError("reel search must stay off unless REEL_FREE_IMAGES=1")

    import production.providers.image.free.integrate as integrate

    previous = integrate.resolve_image_source
    integrate.resolve_image_source = _boom
    try:
        path = wrapped(
            "draw a hammer",
            output_stem="act",
            output_directory=tmp_path,
            free_image_sentence="the hammer",
            free_image_entity="London Hammer",
        )
    finally:
        integrate.resolve_image_source = previous
    assert path.is_file()


def test_technical_judge_failure_retries_without_a_new_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from production.qa.retry import generate_and_gate

    # The default is one retry now; this test drives two.
    monkeypatch.setenv("REEL_QA_JUDGE_RETRIES", "2")

    def _png(path: Path) -> Path:
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 32)
        return path

    generated = {"n": 0}

    def _generate(prompt: str, **kwargs) -> Path:
        del prompt
        generated["n"] += 1
        return _png(tmp_path / f"{kwargs['output_stem']}.png")

    judged = {"n": 0}

    def _judge(path, text, prompt, banned_subjects=None):
        del path, text, prompt, banned_subjects
        judged["n"] += 1
        if judged["n"] < 3:
            return {
                "is_relevant": False,
                "relevance_score": 0.0,
                "qa_status": "unavailable",
                "rejection_reason": "qa_unavailable:timeout",
                "publishable": False,
            }
        return {
            "is_relevant": True,
            "relevance_score": 0.88,
            "qa_status": "scored",
            "rejection_reason": None,
            "publishable": True,
        }

    result = generate_and_gate(
        _generate,
        prompt="the iron hammer",
        chunk_text="The hammer was encased in rock.",
        output_stem="act",
        output_directory=tmp_path,
        channel="ancient_knowledge",
        max_attempts_n=3,
        evaluate_fn=_judge,
        episode_subject=_TOPIC,
    )
    assert generated["n"] == 1
    assert judged["n"] == 3
    assert result.publishable is True
    assert result.first_pass is True
    assert result.extra_generations == 0


def test_grounding_is_scoped_to_ancient_knowledge(monkeypatch) -> None:
    from production.formats.economic_reel.grounding.config import grounding_enabled_for

    monkeypatch.delenv("REEL_VISUAL_GROUNDING", raising=False)
    monkeypatch.delenv("REEL_VISUAL_GROUNDING_CHANNELS", raising=False)
    assert grounding_enabled_for("ancient_knowledge") is True
    assert grounding_enabled_for("master_mei") is False
    assert grounding_enabled_for("") is False
    monkeypatch.setenv("REEL_VISUAL_GROUNDING_CHANNELS", "ancient_knowledge, other_page")
    assert grounding_enabled_for("other_page") is True
    monkeypatch.setenv("REEL_VISUAL_GROUNDING", "0")
    assert grounding_enabled_for("ancient_knowledge") is False
