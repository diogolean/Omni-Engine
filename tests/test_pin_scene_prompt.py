# -*- coding: utf-8 -*-
"""Pin Flux prompts are concrete documentary scenes, not the channel atmosphere."""

from __future__ import annotations

import pytest

from production.formats.pins.pin_copy import (
    build_pin_description,
    finalize_pin_copy,
    keyword_first_title,
    missing_required_credit,
    short_hook,
)
from production.formats.pins.scene_prompt import (
    PIN_NEGATIVE_GUIDANCE,
    composition_line,
    flux_prompt,
    search_constraints,
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
        assert 1 <= len(subject.split()) <= 5, subject
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
    doubled = (
        "Giants in History: Myths, Fossils and Viral Hoaxes: "
        "Giants in History: Myths, Fossils and Viral Hoaxes"
    )
    titled = keyword_first_title(doubled, "Giants")
    assert titled.lower().count("giants in history") == 1
    assert "viral hoaxes: giants" not in titled.lower()
    from shared.pin_text import sanitize_pin_title

    for cut in (doubled[:100], sanitize_pin_title(doubled)):
        collapsed = keyword_first_title(cut, "Giants")
        assert collapsed.lower().count("giants in history") == 1
        assert "viral hoaxes: giants" not in collapsed.lower()
    exact = (
        "Were There Ever Giants? Myths, Fossils and Viral Hoaxes: "
        "Giants in History: Myths, Fossils and Viral"
    )
    assert len(exact) == 100
    collapsed = keyword_first_title(exact, "Giants")
    assert collapsed == (
        "Were There Ever Giants? Myths, Fossils and Viral Hoaxes: Giants in History"
    )
    assert "viral hoaxes: giants in history: myths, fossils and viral" not in collapsed.lower()
    assert collapsed.split()[-1].lower() not in {"and", "of", "in", "the", "viral"}
    from shared.pin_text import fit_pin_title

    assert sanitize_pin_title(exact) == collapsed
    joined = keyword_first_title(
        "Giants in History: Myths, Fossils and Viral Hoaxes",
        "Were There Ever Giants? Myths, Fossils and Viral Hoaxes",
    )
    assert joined.lower().count("myths, fossils and viral") == 1
    assert "viral hoaxes: giants in history: myths" not in joined.lower()
    cut_on_and = fit_pin_title("Stone ruins of the city and UnbrokenTail", 28)
    assert cut_on_and == "Stone ruins of the city"


def test_search_terms_keep_oak_island_and_paititi_specific() -> None:
    oak = search_constraints(_CHANNEL, {"subject_slug": "oak-island", "topic": "Oak Island"})
    assert oak["subject"].lower() == "oak island nova scotia"
    assert "nova scotia" in oak["required_terms"]
    assert "north carolina" in oak["reject_terms"]
    paititi = search_constraints(_CHANNEL, {"subject_slug": "paititi", "topic": "Paititi"})
    assert "paititi" in paititi["subject"].lower()
    assert "paititi" in paititi["required_terms"]
    assert "capitol" in paititi["reject_terms"]
    assert "frog" in paititi["reject_terms"]
    assert "ruins" in paititi["required_any"]
    assert "jungle" in paititi["required_any"]
    assert paititi["allow_biological"] is False
    voynich = search_constraints(_CHANNEL, {"subject_slug": "voynich", "topic": "Voynich"})
    assert voynich["allow_illustration"] is True
    assert "voynich" in voynich["required_terms"]


def test_paititi_hook_ends_on_a_whole_phrase_and_fits_the_card() -> None:
    from production.formats.pins.image_transformer import (
        PIN_W,
        _SERIF_CANDIDATES,
        _find_font,
        _hook_lines,
        _text_width,
    )

    sentence = "A lost Inca city of gold hidden in the Amazon"
    assert short_hook(sentence) == "A lost Inca city of gold hidden"
    lines = _hook_lines(sentence)
    assert lines
    assert len(lines) <= 3
    font = _find_font(_SERIF_CANDIDATES, 54)
    tail = " ".join(lines).split()[-1]
    assert tail not in {"IN", "THE", "OF", "A", "AN"}
    for line in lines:
        assert _text_width(line, font) <= PIN_W - 80
        assert " " not in line or not line.endswith(" ")


def test_credit_is_on_the_card_and_in_the_description_and_blocks_when_missing(
    tmp_path,
) -> None:
    from PIL import Image, ImageDraw

    from production.formats.pins.image_transformer import PinTransformer

    source = tmp_path / "stones.jpg"
    Image.new("RGB", (1200, 1800), (40, 70, 50)).save(source, format="JPEG")
    record = {
        "local_image_path": str(source),
        "topic": "Paititi",
        "pinterest_title": "Paititi",
        "subject_slug": "paititi",
        "variant_index": 0,
        "target_url": "https://ancientknowledge.eokviral.com/paititi",
        "visual_hook": "A lost Inca city of gold hidden in",
        "image_credit": "Ada, CC BY",
        "image_license": "cc_by",
        "image_credit_required": True,
    }
    drawn: list[tuple[str, int]] = []
    original = ImageDraw.ImageDraw.text

    def spy(self, xy, text, *args, **kwargs):
        font = kwargs.get("font")
        size = int(getattr(font, "size", 0) or 0)
        drawn.append((str(text), size))
        return original(self, xy, text, *args, **kwargs)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(ImageDraw.ImageDraw, "text", spy)
    try:
        raw = PinTransformer(outputs_dir=tmp_path / "pins", method="cover").get_pin_bytes(record)
    finally:
        monkeypatch.undo()
    assert raw
    credit_bits = [size for text, size in drawn if text == "Ada, CC BY"]
    assert credit_bits
    assert 14 <= max(credit_bits) <= 20
    description = build_pin_description(record)
    assert "Ada, CC BY" in description
    assert record["target_url"] in description
    assert len(description) <= 500
    bare = dict(record)
    bare.pop("image_credit")
    assert len(build_pin_description(bare)) <= 220
    assert missing_required_credit(bare) is True
    assert missing_required_credit(record) is False
    assert missing_required_credit({"topic": "Paititi"}) is False
