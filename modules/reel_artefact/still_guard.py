# -*- coding: utf-8 -*-
"""Pixel checks for reel stills. Pure Python plus numpy, no model, no OCR.

* ``fingerprint``: md5 of the bytes and a 256-bit difference hash, so the same
  photograph never shows twice in one reel, even as a re-encoded copy.
* ``text_panel``: a slide or museum label (a flat grey field with bright
  strokes on it). Never a scene still, never the cover.
* ``looks_like_photo``: a sketch or flat drawing has almost no sensor texture
  and one dominant tone. The cover and the hook must be a photograph.

The thresholds come from the s23 stills (17 + 16 frames, 4 text panels,
2 sketches, black-and-white photographs included). Env overrides exist for
tuning without a deploy.
"""
from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

logger = logging.getLogger(__name__)

NEAR_DUP_BITS = 24
_BLOCK = 12


def _env_float(name: str, default: float) -> float:
    try:
        return float((os.getenv(name) or "").strip() or default)
    except ValueError:
        return default


def near_dup_bits() -> int:
    return int(_env_float("REEL_NEAR_DUP_BITS", NEAR_DUP_BITS))


@dataclass(frozen=True)
class Traits:
    md5: str
    dhash: int
    text_panel: bool
    photo: bool
    grey_flat: float = 0.0
    panel_text: float = 0.0
    texture: float = 0.0
    tone_peak: float = 0.0


_CACHE: dict[tuple[str, int, int], Traits] = {}


def _dhash(image, size: int = 16) -> int:
    small = image.convert("L").resize((size + 1, size))
    px = list(small.getdata())
    bits = 0
    for row in range(size):
        for col in range(size):
            left = px[row * (size + 1) + col]
            right = px[row * (size + 1) + col + 1]
            bits = (bits << 1) | (left > right)
    return bits


def distance(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def _pixel_traits(image) -> tuple[float, float, float, float]:
    """(grey_flat, panel_text, texture, tone_peak) on a 360x640 thumbnail."""
    import numpy as np

    thumb = image.convert("RGB")
    thumb.thumbnail((360, 640))
    rgb = np.asarray(thumb, dtype=np.int16)
    grey = rgb.mean(axis=2)
    sat = rgb.max(axis=2) - rgb.min(axis=2)
    rows, cols = grey.shape[0] // _BLOCK, grey.shape[1] // _BLOCK
    if rows < 3 or cols < 3:
        return 0.0, 0.0, 99.0, 0.0
    g = grey[: rows * _BLOCK, : cols * _BLOCK]
    s = sat[: rows * _BLOCK, : cols * _BLOCK]
    blocks = g.reshape(rows, _BLOCK, cols, _BLOCK).transpose(0, 2, 1, 3).reshape(rows, cols, -1)
    block_sat = s.reshape(rows, _BLOCK, cols, _BLOCK).transpose(0, 2, 1, 3).reshape(
        rows, cols, -1,
    ).mean(axis=2)
    median = np.median(blocks, axis=2)
    spread = np.abs(blocks - median[..., None])
    near = (spread <= 16).mean(axis=2)
    far = (spread >= 60).mean(axis=2)
    grey_flat = (blocks.std(axis=2) < 5) & (block_sat < 18)
    strokes = (near >= 0.6) & (far >= 0.05) & (far <= 0.4) & (block_sat < 40)
    padded = np.pad(grey_flat, 1)
    neighbours = sum(
        padded[1 + dy: 1 + dy + rows, 1 + dx: 1 + dx + cols]
        for dy in (-1, 0, 1)
        for dx in (-1, 0, 1)
        if (dy, dx) != (0, 0)
    )
    panel_text = float((strokes & (neighbours >= 3)).mean())
    lap = np.abs(
        4 * grey[1:-1, 1:-1] - grey[:-2, 1:-1] - grey[2:, 1:-1] - grey[1:-1, :-2] - grey[1:-1, 2:]
    )
    texture = float(np.percentile(lap, 50))
    hist = np.histogram(grey, bins=32, range=(0, 256))[0] / grey.size
    return float(grey_flat.mean()), panel_text, texture, float(hist.max())


def classify(
    grey_flat: float, panel_text: float, texture: float, tone_peak: float,
) -> tuple[bool, bool]:
    """(text_panel, photo) from the four pixel numbers."""
    panel = (
        grey_flat >= _env_float("REEL_PANEL_FLAT", 0.45)
        and panel_text >= _env_float("REEL_PANEL_TEXT", 0.025)
    ) or panel_text >= _env_float("REEL_PANEL_TEXT_ANY", 0.05)
    # A sketch on paper: one dominant tone and no sensor texture. A studio
    # photograph on a smooth backdrop has little texture but no dominant tone.
    peak_cap = _env_float("REEL_PHOTO_TONE_PEAK", 0.40)
    photo = tone_peak < peak_cap and (
        texture >= _env_float("REEL_PHOTO_TEXTURE", 2.5)
        or tone_peak < _env_float("REEL_PHOTO_SMOOTH_PEAK", 0.25)
    )
    return panel, photo and not panel


def traits(path: str | Path) -> Traits | None:
    """Fingerprint and pixel checks for one file. None when it cannot be read."""
    from PIL import Image

    target = Path(path)
    try:
        stat = target.stat()
    except OSError:
        return None
    key = (str(target.resolve()), int(stat.st_mtime_ns), int(stat.st_size))
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    try:
        data = target.read_bytes()
        with Image.open(target) as image:
            image.load()
            dhash = _dhash(image)
            try:
                numbers = _pixel_traits(image)
            except ImportError:
                numbers = (0.0, 0.0, 99.0, 0.0)
    except Exception as exc:  # noqa: BLE001
        logger.info("still guard could not read %s (%s)", target.name, type(exc).__name__)
        return None
    panel, photo = classify(*numbers)
    result = Traits(
        md5=hashlib.md5(data).hexdigest(),  # noqa: S324 - identity, not security
        dhash=dhash,
        text_panel=panel,
        photo=photo,
        grey_flat=numbers[0],
        panel_text=numbers[1],
        texture=numbers[2],
        tone_peak=numbers[3],
    )
    _CACHE[key] = result
    return result


def same_picture(a: Traits, b: Traits, *, bits: int | None = None) -> bool:
    limit = near_dup_bits() if bits is None else int(bits)
    return a.md5 == b.md5 or distance(a.dhash, b.dhash) <= limit


def repeats(paths: Iterable[str | Path | None]) -> list[tuple[int, int]]:
    """(earlier, later) beat pairs that show the same picture."""
    seen: list[tuple[int, Traits]] = []
    pairs: list[tuple[int, int]] = []
    for index, path in enumerate(paths):
        if not path:
            continue
        info = traits(path)
        if info is None:
            continue
        for earlier, other in seen:
            if same_picture(other, info):
                pairs.append((earlier, index))
                break
        else:
            seen.append((index, info))
    return pairs


def enforce_unique(
    paths: list[Path],
    replacement: Callable[[int, list[Traits]], Path | None],
) -> tuple[list[Path], list[int]]:
    """Replace every later copy of a picture already used in the reel.

    ``replacement(beat_index, used)`` returns a new still for that beat or
    None. A beat that gets nothing keeps its still and is reported.
    """
    out = list(paths)
    used: list[Traits] = []
    stuck: list[int] = []
    for index, path in enumerate(out):
        info = traits(path) if path else None
        if info is None:
            continue
        if not any(same_picture(info, other) for other in used):
            used.append(info)
            continue
        fresh = replacement(index, list(used))
        fresh_info = traits(fresh) if fresh else None
        if fresh_info is not None and not any(same_picture(fresh_info, o) for o in used):
            logger.info("REPEAT GUARD | beat %d replaced (%s)", index, Path(fresh).name)
            out[index] = Path(fresh)
            used.append(fresh_info)
            continue
        logger.warning("REPEAT GUARD | beat %d still repeats; nothing unused left", index)
        stuck.append(index)
        used.append(info)
    return out, stuck


def reset_cache() -> None:
    _CACHE.clear()
