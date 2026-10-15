"""Thumbnail titles are never cut mid-sentence."""

from __future__ import annotations

from PIL import Image, ImageDraw

from core.animator.thumbnail_generator import _font, _wrap_title

LONG = "The next update erases your personality. Do you mourn the old you?"


def test_wrap_title_can_return_every_line() -> None:
    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    full = _wrap_title(draw, LONG, _font(112), 1640, max_lines=None)
    assert len(full) == 3
    assert _wrap_title(draw, LONG, _font(112), 1640) == full[:2]


def test_a_smaller_size_fits_the_whole_title_on_two_lines() -> None:
    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    fits = [
        size
        for size in (112, 98, 86, 76, 68)
        if len(_wrap_title(draw, LONG, _font(size), 1640, max_lines=None)) <= 2
    ]
    assert fits and fits[0] < 112
    lines = _wrap_title(draw, LONG, _font(fits[0]), 1640, max_lines=None)
    assert " ".join(lines) == LONG.upper()
