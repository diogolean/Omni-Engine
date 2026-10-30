# -*- coding: utf-8 -*-
"""The pin card is a large sans title, one fixed band, and a fine-print credit."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from production.formats.pins.credit_text import (
    repair_credit_encoding,
    shape_credit_line,
)
from production.formats.pins.image_transformer import (
    PIN_H,
    PIN_W,
    PinTransformer,
    _center_crop_to_size,
    _font_covers,
    _frame_photo,
    cover_keeps_enough,
    pin_text_position,
)


def _two_tone(path: Path, width: int, height: int) -> None:
    image = Image.new("RGB", (width, height), (0, 180, 40))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, width, max(8, height // 12)), fill=(220, 20, 20))
    image.save(path, format="JPEG")


def test_portrait_fills_the_frame_and_a_wide_photo_keeps_the_blur() -> None:
    tall = Image.new("RGB", (1200, 1600), (10, 20, 30))
    wide = Image.new("RGB", (1600, 900), (10, 20, 30))
    assert cover_keeps_enough(tall) is True
    assert cover_keeps_enough(wide) is False


def test_rendered_portrait_is_cropped_and_the_wide_photo_is_padded(
    tmp_path: Path,
) -> None:
    tall = Image.new("RGB", (1200, 1600), (20, 40, 60))
    wide = Image.new("RGB", (1600, 900), (20, 40, 60))
    ImageDraw.Draw(tall).rectangle((0, 0, 40, 1600), fill=(255, 0, 0))
    ImageDraw.Draw(wide).rectangle((0, 0, 1600, 40), fill=(255, 0, 0))
    assert list(_frame_photo(tall, "blurred_padding").getdata()) == list(
        _center_crop_to_size(tall, PIN_W, PIN_H).getdata()
    )
    wide_frame = _frame_photo(wide, "blurred_padding")
    assert list(wide_frame.getdata()) != list(
        _center_crop_to_size(wide, PIN_W, PIN_H).getdata()
    )
    card = PinTransformer(outputs_dir=tmp_path / "pins")._compose(wide, "BAALBEK TRILITHON")
    assert card.size == (PIN_W, PIN_H)


def test_title_band_stays_where_the_channel_puts_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "stone.jpg"
    _two_tone(source, 1200, 1600)
    drawn: list[tuple[int, str]] = []
    original = ImageDraw.ImageDraw.text

    def spy(self, xy, text, *args, **kwargs):
        drawn.append((int(xy[1]), str(text)))
        return original(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", spy)
    monkeypatch.setattr(
        "shared.channel_flags.value",
        lambda _page, name, default=None: "bottom" if name == "PIN_TEXT_POSITION" else default,
    )
    assert pin_text_position() == "bottom"
    raw = PinTransformer(outputs_dir=tmp_path / "pins").get_pin_bytes(
        {
            "local_image_path": str(source),
            "topic": "Baalbek trilithon",
            "visual_hook": "Baalbek trilithon",
            "subject_slug": "baalbek",
            "variant_index": 0,
        }
    )
    assert raw
    tops = [y for y, text in drawn if "BAALBEK" in text.upper()]
    assert tops
    assert min(tops) > PIN_H // 2


def test_arabic_wikimedia_credit_is_repaired_and_drawn_with_a_covering_font(
    tmp_path: Path,
) -> None:
    mojibake = "عبدالرحمن".encode("utf-8").decode("latin-1") + ", CC BY"
    assert repair_credit_encoding(mojibake) == "عبدالرحمن, CC BY"
    shown = shape_credit_line(mojibake)
    assert "CC BY" in shown
    assert shown != mojibake
    source = tmp_path / "stone.jpg"
    _two_tone(source, 1200, 1600)
    drawn: list[tuple[str, str]] = []
    original = ImageDraw.ImageDraw.text

    def spy(self, xy, text, *args, **kwargs):
        font = kwargs.get("font")
        path = str(getattr(font, "path", ""))
        drawn.append((str(text), path))
        return original(self, xy, text, *args, **kwargs)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(ImageDraw.ImageDraw, "text", spy)
    try:
        raw = PinTransformer(outputs_dir=tmp_path / "pins").get_pin_bytes(
            {
                "local_image_path": str(source),
                "topic": "Baghdad battery",
                "visual_hook": "The Baghdad battery",
                "subject_slug": "battery",
                "variant_index": 0,
                "image_credit": mojibake,
                "image_license": "cc_by",
            }
        )
    finally:
        monkeypatch.undo()
    assert raw
    credits = [(text, path) for text, path in drawn if "CC BY" in text]
    assert credits
    text, path = credits[0]
    assert text == shown
    assert "DejaVu" in Path(path).name
    from PIL import ImageFont

    font = ImageFont.truetype(path, 18)
    assert _font_covers(font, text)
