# -*- coding: utf-8 -*-
"""Reject a download that is blank, tiny, or the wrong shape to be a still."""
from __future__ import annotations

from PIL import Image


def image_defect(image: Image.Image, *, nbytes: int = 0) -> str:
    """Empty string when the picture can be a reel still.

    A solid placeholder (stddev near 0), a tiny file, or a banner-shaped
    image is not a photograph of the event or the artefact.
    """
    width, height = image.size
    if width < 64 or height < 64:
        return "tiny"
    short = min(width, height)
    long = max(width, height)
    if short <= 0 or (long / short) > 3.5:
        return "aspect"
    if nbytes and nbytes < 1024 and long >= 720:
        return "tiny-file"
    sample = image.convert("L").resize((24, 24), Image.Resampling.BOX)
    values = list(sample.getdata())
    count = len(values) or 1
    mean = sum(values) / count
    variance = sum((value - mean) ** 2 for value in values) / count
    if variance ** 0.5 < 2.0:
        return "blank"
    return ""
