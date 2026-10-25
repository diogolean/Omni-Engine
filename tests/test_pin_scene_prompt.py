# -*- coding: utf-8 -*-
"""Pin Flux prompts are concrete documentary scenes, not the channel atmosphere."""

from __future__ import annotations

import pytest

from production.formats.pins.pin_copy import finalize_pin_copy, keyword_first_title
from production.formats.pins.scene_prompt import (
    PIN_NEGATIVE_GUIDANCE,
    composition_line,
    flux_prompt,
    search_subject,
    strip_atmosphere,
)

_CHANNEL = "ancient_knowledge"
_ATMOSPHERE = (
    "volumetric light shafts",
    "rim lighting",
    "ethereal glow",
    "alien glyphs",
    "chiaroscuro",
    "torchlight",
)
_ARTICLES = (
    "atlantis",
    "baalbek",
    "crystal-skulls",
    "dendera-light",
    "easter-island-moai",
    "giants",
    "nazca",
    "oak-island",
    "paititi",
    "sumerian",
    "tartaria",
    "underwater-cities",
    "voynich",
)


def _clean(prompt: str) -> None:
    assert PIN_NEGATIVE_GUIDANCE in prompt
    scene = prompt.split(PIN_NEGATIVE_GUIDANCE, 1)[0].lower()
    for phrase in _ATMOSPHERE:
        assert phrase not in scene
    assert "halo" not in scene
    assert "light beam" not in scene
    assert "light shaft" not in scene


def test_named_subjects_use_the_entity_scene_not_the_atmosphere() -> None:
    cases = {
        "voynich": "open old vellum manuscript",
        "crystal skulls": "clear quartz crystal skull",
        "easter island moai": "grassy hillside",
        "paititi": "dense peruvian jungle",
    }
    called = {"n": 0}

    def _boom(*_args, **_kwargs):
        called["n"] += 1
        raise AssertionError("entity scene must skip the text model")

    for topic, needle in cases.items():
        prompt = flux_prompt(topic, _CHANNEL, title=topic, complete=_boom)
        assert needle in prompt.lower()
        _clean(prompt)
        assert "documentary photograph" in prompt.lower()
    assert called["n"] == 0


def test_every_current_article_has_a_scene_and_a_short_query() -> None:
    scenes = set()
    compositions = set()
    for slug in _ARTICLES:
        entry = {"subject_slug": slug, "topic": slug.replace("-", " ")}
        subject = search_subject(_CHANNEL, entry)
        assert 1 <= len(subject.split()) <= 3, subject
        prompt = flux_prompt(slug, _CHANNEL, title=entry["topic"], allow_model=False)
        _clean(prompt)
        scenes.add(prompt.split("Documentary photograph", 1)[-1][:80])
        compositions.add(composition_line(entry["topic"]))
    assert len(scenes) == len(_ARTICLES)
    assert len(compositions) > 1


def test_model_scene_drops_halo_language_and_uses_the_template() -> None:
    seen: dict[str, str] = {}

    def complete(prompt: str, *, system: str) -> str:
        seen["prompt"] = prompt
        seen["system"] = system
        return (
            '{"scene": "An open manuscript on a desk, with a bright halo '
            'and volumetric light shafts."}'
        )

    prompt = flux_prompt(
        "unmatched subject xyz",
        _CHANNEL,
        title="Unmatched Subject Xyz",
        article="A short article body.",
        complete=complete,
    )
    assert "Title: Unmatched Subject Xyz" in seen["prompt"]
    assert "A short article body." in seen["prompt"]
    assert "natural daylight" in seen["system"].lower()
    scene = prompt.split(PIN_NEGATIVE_GUIDANCE, 1)[0].lower()
    assert "volumetric" not in scene
    assert "halo" not in scene
    assert "open manuscript on a desk" in scene
    _clean(prompt)


def test_atmosphere_clauses_are_removed() -> None:
    cleaned = strip_atmosphere(
        "Stone blocks in daylight, volumetric light shafts, strong rim lighting"
    )
    assert cleaned == "Stone blocks in daylight"


def test_giants_title_is_not_repeated_and_captions_fit() -> None:
    assert keyword_first_title("Giants: Giants", "Giants") == "Giants"
    assert keyword_first_title("Giants Giants", "Giants") == "Giants"
    titled = keyword_first_title(
        "Giants",
        "The giants who left bones that do not fit the graves",
    )
    assert titled.lower().count("giants") == 1
    assert "giants: giants" not in keyword_first_title("Giants", "Ancient Giants").lower()
    entry = {
        "topic": "Giants",
        "pinterest_title": "Giants: Giants",
        "pinterest_caption": "bone " * 200,
    }
    finalize_pin_copy(entry)
    assert entry["pinterest_title"] == "Giants"
    assert len(entry["pinterest_caption"]) <= 500
    assert entry["pinterest_caption"].endswith("bone") or not entry["pinterest_caption"].endswith("b")
