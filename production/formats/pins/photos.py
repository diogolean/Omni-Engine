# -*- coding: utf-8 -*-
"""Tell a photograph from a flat title graphic."""

from __future__ import annotations

from pathlib import Path


def image_is_text_graphic(img) -> bool:
    """True for flat title cards (dark field, few colors), false for photographs."""
    from PIL import Image  # noqa: PLC0415

    small = img.convert("RGB").resize((48, 72), Image.Resampling.BOX)
    pixels = list(small.getdata())
    count = len(pixels)
    if count == 0:
        return True
    mean = [sum(pixel[channel] for pixel in pixels) / count for channel in range(3)]
    variance = sum(
        (pixel[0] - mean[0]) ** 2
        + (pixel[1] - mean[1]) ** 2
        + (pixel[2] - mean[2]) ** 2
        for pixel in pixels
    ) / count
    spread = variance ** 0.5
    buckets: dict[tuple[int, int, int], int] = {}
    for pixel in pixels:
        key = (pixel[0] // 16, pixel[1] // 16, pixel[2] // 16)
        buckets[key] = buckets.get(key, 0) + 1
    dominant = max(buckets.values()) / count
    if spread < 26 and dominant >= 0.34:
        return True
    if dominant >= 0.48:
        return True
    teal_field = (
        mean[0] < 55
        and 35 <= mean[1] <= 125
        and 45 <= mean[2] <= 145
        and dominant >= 0.36
    )
    return teal_field


def is_text_graphic(path: Path) -> bool:
    """True when the file is a title graphic rather than a photograph."""
    from PIL import Image  # noqa: PLC0415

    try:
        with Image.open(path) as img:
            return image_is_text_graphic(img)
    except Exception:  # noqa: BLE001
        return True
