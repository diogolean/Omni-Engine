# -*- coding: utf-8 -*-
"""Pixel checks for reel stills. Pure Python plus numpy, no model, no OCR.

* ``fingerprint``: md5 of the bytes and a 256-bit difference hash, so the same
  photograph never shows twice in one reel, even as a re-encoded copy.
* ``core_dhash``: the difference hash of the picture after blur-padded or
  letterbox bands are trimmed, cut to the reel's 9:16 centre. A blur-padded
  still and the full-frame crop of the same photograph share it.
* ``crop_match``: ORB keypoints and a RANSAC similarity fit (OpenCV, when
  installed). A different crop or window of one photograph matches; another
  photograph of the same monument does not.
* ``source_key``: the pool key or source URL from the still's sidecar. Two
  stills cut from one source file are one photograph.
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
from dataclasses import dataclass, field, replace
from pathlib import Path
from utils.env import env_float
from typing import Callable, Iterable

logger = logging.getLogger(__name__)

NEAR_DUP_BITS = 24
_BLOCK = 12


_env_float = env_float


def near_dup_bits() -> int:
    return int(_env_float("REEL_NEAR_DUP_BITS", NEAR_DUP_BITS))


CORE_DUP_BITS = 32


def core_dup_bits() -> int:
    """Core hashes of one photograph sat at 6-22 bits in s23b; others at 83+."""
    return int(_env_float("REEL_CORE_DUP_BITS", CORE_DUP_BITS))


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
    core_dhash: int = 0
    source_key: str = ""
    path: str = field(default="", compare=False)


_CACHE: dict[tuple[str, int, int], Traits] = {}
_FEATURES: dict[tuple[str, int, int], object] = {}


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


def _band_rows(energy: list[float]) -> tuple[int, int]:
    """Low-detail rows at both ends: (top count, bottom count)."""
    count = len(energy)
    if count < 16:
        return 0, 0
    middle = sorted(energy[count // 4: 3 * count // 4])
    floor = max(0.6, 0.25 * middle[len(middle) // 2])
    top = 0
    while top < count // 2 and energy[top] < floor:
        top += 1
    bottom = 0
    while bottom < count // 2 and energy[count - 1 - bottom] < floor:
        bottom += 1
    return top, bottom


def _pad(top: int, bottom: int, count: int) -> int:
    """Padding from a centred fit is the same height above and below.

    A flat sky next to the top band makes that side look taller, so the
    smaller side is the pad. One band alone is part of the photograph.
    """
    pad = min(top, bottom)
    return pad if pad >= 0.04 * count else 0


def content_region(image):
    """The picture without blur-padded or letterbox bands.

    Only bands of matching size at both ends count, so a flat sky at the
    top of a photograph stays part of it.
    """
    import numpy as np

    grey = image.convert("L")
    grey.thumbnail((360, 640))
    pixels = np.asarray(grey, dtype=np.float32)
    # Fine detail per row and column. A blur pad has almost none; the
    # Laplacian ignores the smooth colour that a blur leaves behind.
    lap = np.zeros_like(pixels)
    lap[1:-1, 1:-1] = np.abs(
        4 * pixels[1:-1, 1:-1]
        - pixels[:-2, 1:-1] - pixels[2:, 1:-1] - pixels[1:-1, :-2] - pixels[1:-1, 2:]
    )
    rows = lap.mean(axis=1).tolist()
    cols = lap.mean(axis=0).tolist()
    rows[0] = rows[1] if len(rows) > 1 else rows[0]
    rows[-1] = rows[-2] if len(rows) > 1 else rows[-1]
    cols[0] = cols[1] if len(cols) > 1 else cols[0]
    cols[-1] = cols[-2] if len(cols) > 1 else cols[-1]
    height, width = pixels.shape
    top, bottom = _band_rows(rows)
    left, right = _band_rows(cols)
    box = [0, 0, image.size[0], image.size[1]]
    sx = image.size[0] / max(1, width)
    sy = image.size[1] / max(1, height)
    rows_pad = _pad(top, bottom, height)
    cols_pad = _pad(left, right, width)
    if rows_pad:
        box[1] = int(round(rows_pad * sy))
        box[3] = int(round((height - rows_pad) * sy))
    if cols_pad:
        box[0] = int(round(cols_pad * sx))
        box[2] = int(round((width - cols_pad) * sx))
    if box[2] - box[0] < 16 or box[3] - box[1] < 16:
        return image
    return image.crop(tuple(box))


def _reel_window(image):
    """The centre 9:16 window, the one ``prepare_reel_frame`` cuts."""
    width, height = image.size
    if width / max(1, height) > 9 / 16:
        crop_w = max(1, int(round(height * 9 / 16)))
        left = (width - crop_w) // 2
        return image.crop((left, 0, left + crop_w, height))
    crop_h = max(1, int(round(width * 16 / 9)))
    top = (height - crop_h) // 2
    return image.crop((0, top, width, top + crop_h))


def core_dhash(image) -> int:
    return _dhash(_reel_window(content_region(image)))


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
        return _with_source(hit, target)
    try:
        data = target.read_bytes()
        with Image.open(target) as image:
            image.load()
            dhash = _dhash(image)
            try:
                numbers = _pixel_traits(image)
                core = core_dhash(image)
            except ImportError:
                numbers = (0.0, 0.0, 99.0, 0.0)
                core = 0
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
        core_dhash=core,
        path=str(target),
    )
    _CACHE[key] = result
    return _with_source(result, target)


def _with_source(found: Traits, target: Path) -> Traits:
    """Attach the pool key or source URL from the sidecar, read fresh each time."""
    key = source_key_for(target)
    if key == found.source_key:
        return found
    return replace(found, source_key=key)


def source_key_for(path: str | Path) -> str:
    try:
        from modules.reel_artefact.source import source_meta

        meta = source_meta(Path(path)) or {}
    except Exception:  # noqa: BLE001
        return ""
    for name in ("pool_key", "source_url", "reuse_key"):
        value = str(meta.get(name) or "").strip()
        if value:
            return value.split("?")[0].lower()
    return ""


def with_source_key(found: Traits | None, key: str) -> Traits | None:
    """A pool file has no sidecar; its pool key names the source."""
    if found is None or not key:
        return found
    return replace(found, source_key=str(key).split("?")[0].lower())


def _orb_enabled() -> bool:
    return (os.getenv("REEL_ORB_MATCH") or "1").strip().lower() not in {"0", "false", "off", "no"}


def _features(path: str):
    """ORB keypoints on a 480px greyscale copy. None without OpenCV."""
    if not path:
        return None
    try:
        stat = Path(path).stat()
    except OSError:
        return None
    cache_key = (str(path), int(stat.st_mtime_ns), int(stat.st_size))
    hit = _FEATURES.get(cache_key)
    if hit is not None:
        return hit or None
    try:
        import cv2  # type: ignore[import-not-found]
        import numpy as np
        from PIL import Image

        with Image.open(path) as opened:
            grey = opened.convert("L")
            grey.thumbnail((480, 480))
            pixels = np.asarray(grey, dtype=np.uint8)
        orb = cv2.ORB_create(nfeatures=600, fastThreshold=12)
        keys, desc = orb.detectAndCompute(pixels, None)
        points = np.float32([k.pt for k in keys]) if keys else None
        found = (points, desc, pixels.shape) if desc is not None and len(keys) >= 8 else ()
    except Exception:  # noqa: BLE001 - no OpenCV, unreadable file
        found = ()
    _FEATURES[cache_key] = found
    return found or None


def crop_match(a: Traits, b: Traits) -> bool:
    """One photograph in two crops: many ORB matches that agree on one
    similarity transform, with a sane scale, no rotation, and inliers spread
    over both pictures. A different photograph of the same place collapses
    to a degenerate fit and fails.
    """
    if not _orb_enabled():
        return False
    left = _features(a.path)
    right = _features(b.path)
    if left is None or right is None:
        return False
    try:
        import cv2  # type: ignore[import-not-found]
        import numpy as np

        pts_a, desc_a, shape_a = left
        pts_b, desc_b, shape_b = right
        pairs = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(desc_a, desc_b, k=2)
        good = [m[0] for m in pairs if len(m) == 2 and m[0].distance < 0.8 * m[1].distance]
        need = int(_env_float("REEL_ORB_MIN_INLIERS", 20))
        if len(good) < need:
            return False
        src = np.float32([pts_a[m.queryIdx] for m in good])
        dst = np.float32([pts_b[m.trainIdx] for m in good])
        model, mask = cv2.estimateAffinePartial2D(
            src, dst, method=cv2.RANSAC, ransacReprojThreshold=4.0,
        )
        if model is None or mask is None:
            return False
        inliers = mask.ravel().astype(bool)
        if int(inliers.sum()) < need:
            return False
        scale = float(np.hypot(model[0, 0], model[1, 0]))
        angle = abs(float(np.degrees(np.arctan2(model[1, 0], model[0, 0]))))
        if not 0.15 <= scale <= 6.0 or angle > 8.0:
            return False

        def spread(points, shape) -> float:
            box = points[inliers]
            area = float(np.prod(box.max(axis=0) - box.min(axis=0)))
            return area / float(max(1, shape[0] * shape[1]))

        return min(spread(src, shape_a), spread(dst, shape_b)) >= 0.01
    except Exception:  # noqa: BLE001
        return False


def same_picture(a: Traits, b: Traits, *, bits: int | None = None) -> bool:
    """Same file, same pixels, same source, or a crop of the same photograph."""
    limit = near_dup_bits() if bits is None else int(bits)
    if a.md5 == b.md5 or distance(a.dhash, b.dhash) <= limit:
        return True
    if a.source_key and a.source_key == b.source_key:
        return True
    core_limit = core_dup_bits() if bits is None else int(bits)
    if a.core_dhash and b.core_dhash and distance(a.core_dhash, b.core_dhash) <= core_limit:
        return True
    return crop_match(a, b)


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
    _FEATURES.clear()
