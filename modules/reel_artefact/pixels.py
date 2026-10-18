# -*- coding: utf-8 -*-
"""Reject a download that is blank, tiny, synthetic, or the wrong shape."""
from __future__ import annotations

import math
from collections import Counter

from PIL import Image


def image_defect(image: Image.Image, *, nbytes: int = 0) -> str:
    """Empty string when the picture can be a reel still.

    A solid placeholder, a checkerboard, a very low-entropy pattern, a
    tiny file, or a banner-shaped image is not a photograph.
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
    sample = image.convert("L").resize((48, 48), Image.Resampling.BOX)
    values = list(sample.getdata())
    count = len(values) or 1
    mean = sum(values) / count
    variance = sum((value - mean) ** 2 for value in values) / count
    if variance ** 0.5 < 2.0:
        return "blank"
    if _checkerboard(values, 48):
        return "checkerboard"
    if _low_entropy(values):
        return "synthetic"
    return ""


def _low_entropy(values: list[int]) -> bool:
    """True when a handful of tones make up the whole frame."""
    count = len(values) or 1
    if len(set(values)) <= 8:
        return True
    entropy = 0.0
    for seen in Counter(values).values():
        prob = seen / count
        entropy -= prob * math.log2(prob)
    return entropy < 2.0


def _checkerboard(values: list[int], width: int) -> bool:
    """Two flat tones on alternating cells, the usual test fixture."""
    even: list[int] = []
    odd: list[int] = []
    for index, value in enumerate(values):
        x = index % width
        y = index // width
        if (x + y) % 2 == 0:
            even.append(value)
        else:
            odd.append(value)
    if not even or not odd:
        return False
    mean_even = sum(even) / len(even)
    mean_odd = sum(odd) / len(odd)
    if abs(mean_even - mean_odd) < 18:
        return False
    return _std(even, mean_even) < 18 and _std(odd, mean_odd) < 18


def _std(values: list[int], mean: float) -> float:
    if not values:
        return 0.0
    return (sum((value - mean) ** 2 for value in values) / len(values)) ** 0.5
