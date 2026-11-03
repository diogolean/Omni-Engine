# -*- coding: utf-8 -*-
"""Remove the burned-in subtitle band before the final-frame judge."""
from __future__ import annotations

from pathlib import Path

from PIL import Image

# Subtitles are drawn near 82% of the frame height, two lines.
_SUBTITLE_TOP = 0.76


def strip_subtitle_band(src: Path | str, dest: Path | str) -> Path | None:
    """Crop the bottom subtitle band. None when the file is not an image.

    The judge then sees the same picture the still gate saw, without the
    phrase layer that made a passing still fail as non-photoreal.
    """
    source = Path(src)
    target = Path(dest)
    try:
        with Image.open(source) as opened:
            image = opened.convert("RGB")
    except Exception:  # noqa: BLE001 — a probe frame may be a stub
        return None
    width, height = image.size
    if height < 64 or width < 32:
        return None
    kept = max(32, int(height * _SUBTITLE_TOP))
    cropped = image.crop((0, 0, width, kept))
    target.parent.mkdir(parents=True, exist_ok=True)
    cropped.save(target, format="JPEG", quality=90)
    return target
