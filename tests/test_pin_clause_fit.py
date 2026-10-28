# -*- coding: utf-8 -*-
"""Pin titles and card hooks end on a clause. The card keeps the predicate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from production.formats.pins.image_transformer import (
    PIN_W,
    _HOOK_FONT_SIZE,
    _HOOK_LINE_HEIGHT,
    _SERIF_CANDIDATES,
    _find_font,
    _hook_layout,
    _hook_line_height,
    _text_width,
)
from production.formats.pins.pin_copy import card_hook_text, short_hook, theme_string
from shared.pin_text import fit_pin_title, is_dangling_pin_word

_NAZCA = (
    "The Nazca Lines — vast geoglyphs only visible and meaningful "
    "from 1,500 feet in the air"
)
_WHO_MADE = (
    "The Nazca Lines: Who Made Them and Why These Geoglyphs Only "
    "Make Sense From the Air Above the Pale Desert Plateau"
)


def test_long_topic_title_ends_on_a_clause_within_100() -> None:
    from distribution.scheduling.inventory import build_title_template, build_visual_hook
    assert len(_WHO_MADE) > 100
    fitted = fit_pin_title(_WHO_MADE)
    assert fitted == "The Nazca Lines"
    assert len(fitted) <= 100
    assert not is_dangling_pin_word(fitted.split()[-1])
    assert not fitted.upper().endswith("WHO MADE")
    assert build_title_template(_WHO_MADE) == "The Nazca Lines"
    overlay = build_visual_hook(_WHO_MADE)
    assert "THEM" in overlay and "AIR" in overlay
    assert not overlay.endswith("WHO MADE")

    kept = "The Nazca Lines: Who Made Them. " + ("further evidence " * 12)
    assert len(kept) > 100
    assert fit_pin_title(kept) == "The Nazca Lines: Who Made Them."

    assert len(_NAZCA) <= 100
    whole = fit_pin_title(_NAZCA)
    assert whole == _NAZCA
    assert not is_dangling_pin_word(whole.split()[-1])
    assert whole.lower().endswith("air")


def test_clauseless_title_still_ends_on_a_word() -> None:
    cut = fit_pin_title("Stone ruins of the city and UnbrokenTail", 28)
    assert cut == "Stone ruins of the city"
    assert not is_dangling_pin_word(cut.split()[-1])
    assert "Unbroken" not in cut


def _theme(monkeypatch: pytest.MonkeyPatch) -> None:
    from production.formats.pins import config as cfg

    pack = json.loads(
        Path("channels_config/ancient_knowledge/config.json").read_text(encoding="utf-8")
    )
    theme = dict(pack["pin_theme"])
    assert theme["hook_template"] == "{topic}"
    monkeypatch.setattr(cfg, "PIN_THEME", theme, raising=False)
    monkeypatch.setattr(cfg, "CTA_BUTTON_LABEL", theme.get("cta_label") or "", raising=False)


def test_card_hook_keeps_the_predicate_instead_of_who_made(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _theme(monkeypatch)
    assert theme_string("hook_template") == "{topic}"

    record = {
        "topic": _WHO_MADE,
        "pinterest_title": _WHO_MADE,
        "visual_hook": "THE NAZCA LINES: WHO MADE",
    }
    hook = card_hook_text(record)
    assert "them" in hook.lower()
    assert "air" in hook.lower()
    assert not hook.upper().rstrip(".").endswith("WHO MADE")
    assert not is_dangling_pin_word(hook.split()[-1])

    lines, size = _hook_layout(hook)
    shown = " ".join(lines).split()
    assert "THEM" in shown
    assert "AIR" in shown
    assert not is_dangling_pin_word(shown[-1])
    assert len(lines) <= 3
    assert size < _HOOK_FONT_SIZE
    font = _find_font(_SERIF_CANDIDATES, size)
    for line in lines:
        assert _text_width(line, font) <= PIN_W - 80


def test_short_hook_keeps_the_original_type_size() -> None:
    lines, size = _hook_layout("Baalbek trilithon")
    assert lines == ["BAALBEK TRILITHON"]
    assert size == _HOOK_FONT_SIZE
    assert _hook_line_height(size) == _HOOK_LINE_HEIGHT
    sentence = "A lost Inca city of gold hidden in the Amazon"
    assert short_hook(sentence) == "A lost Inca city of gold hidden"
    kept, kept_size = _hook_layout(sentence)
    assert kept_size == _HOOK_FONT_SIZE
    assert " ".join(kept).split()[-1] == "HIDDEN"


def test_drawn_hook_shrinks_and_the_cta_stays(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from PIL import Image, ImageDraw

    from production.formats.pins.image_transformer import PinTransformer

    _theme(monkeypatch)
    source = tmp_path / "nazca.jpg"
    Image.new("RGB", (900, 1400), (40, 70, 50)).save(source, format="JPEG")
    record = {
        "local_image_path": str(source),
        "topic": _WHO_MADE,
        "pinterest_title": _WHO_MADE,
        "visual_hook": "THE NAZCA LINES: WHO MADE",
        "subject_slug": "nazca",
        "variant_index": 0,
    }
    drawn: list[tuple[str, int]] = []
    original = ImageDraw.ImageDraw.text

    def spy(self, xy, text, *args, **kwargs):
        font = kwargs.get("font")
        size = int(getattr(font, "size", 0) or 0)
        drawn.append((str(text), size))
        return original(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", spy)
    raw = PinTransformer(outputs_dir=tmp_path / "pins", method="cover").get_pin_bytes(record)
    assert raw
    blob = " ".join(text for text, _size in drawn).upper()
    assert "THEM" in blob
    assert "AIR" in blob
    assert "WHO MADE THEM" in blob
    assert "READ THE FULL STORY ->" in blob
    hook_sizes = [size for text, size in drawn if "NAZCA" in text.upper() or "THEM" in text.upper()]
    assert hook_sizes
    assert max(hook_sizes) < _HOOK_FONT_SIZE
    assert max(hook_sizes) >= 22
