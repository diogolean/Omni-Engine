# -*- coding: utf-8 -*-
"""The repeat guard catches padded copies and other crops of one photograph.

The geometries are the s23b repeats. Reel A showed four store-cache photos
twice: once as the reel crop of photo_N and once as still_N, the blur-padded
fit of the same file (tiles #1/#5, #2/#6, #3/#8, #4/#9; sources 1280x1687,
1280x850, 3264x2448 and 2736x3648). Reel B showed the 960x700 Ctesiphon 1932
photo in two windows (tiles #5/#9), the second through the Flux fallback.
The photographs are synthetic so the repo carries no third-party pictures.
"""
from __future__ import annotations

import random
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from modules.reel_artefact import still_guard
from modules.reel_artefact.crop import _blur_fill, prepare_reel_frame

S23B_A_SOURCES = [(1280, 1687), (1280, 850), (3264, 2448), (2736, 3648)]


def _photo(size: tuple[int, int], seed: int) -> Image.Image:
    """A textured scene: sky gradient, ground, many hard-edged shapes."""
    rng = random.Random(seed)
    # Same aspect as the s23b source, drawn small to keep the test fast.
    scale = min(1.0, 900 / max(size))
    width, height = max(2, int(size[0] * scale)), max(2, int(size[1] * scale))
    size = (width, height)
    image = Image.new("RGB", size)
    draw = ImageDraw.Draw(image)
    horizon = int(height * rng.uniform(0.3, 0.5))
    for y in range(height):
        if y < horizon:
            tone = 150 + int(60 * y / max(1, horizon))
            draw.line([(0, y), (width, y)], fill=(tone - 40, tone - 20, tone))
        else:
            tone = 90 + int(50 * (y - horizon) / max(1, height - horizon))
            draw.line([(0, y), (width, y)], fill=(tone + 30, tone + 10, tone - 20))
    unit = max(width, height) / 100
    for _ in range(160):
        x = rng.uniform(0, width)
        y = rng.uniform(horizon * 0.8, height)
        w = rng.uniform(2, 9) * unit
        h = rng.uniform(2, 12) * unit
        colour = tuple(rng.randrange(30, 230) for _ in range(3))
        if rng.random() < 0.5:
            draw.rectangle([x, y, x + w, y + h], fill=colour, outline=(20, 20, 20))
        else:
            draw.ellipse([x, y, x + w, y + h], fill=colour, outline=(240, 240, 240))
    noise = Image.effect_noise(size, 18).convert("RGB")
    return Image.blend(image, noise, 0.10)


def _save(image: Image.Image, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, quality=90)
    return path


@pytest.fixture(autouse=True)
def _fresh_guard():
    still_guard.reset_cache()
    yield
    still_guard.reset_cache()


@pytest.mark.parametrize("size", S23B_A_SOURCES)
def test_blur_padded_copy_and_reel_crop_share_a_core_hash(
    size, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """s23b A: photo_N cropped for the reel, still_N padded. One picture."""
    monkeypatch.setenv("REEL_ORB_MATCH", "0")
    source = _photo(size, seed=size[0] + size[1])
    crop, _how = prepare_reel_frame(source, "center")
    padded = _blur_fill(source)
    first = still_guard.traits(_save(crop, tmp_path / "photo.jpg"))
    second = still_guard.traits(_save(padded, tmp_path / "still.jpg"))
    assert first is not None and second is not None
    # The old full-frame hash missed these (s23b distance 64 and up).
    assert still_guard.distance(first.dhash, second.dhash) > still_guard.near_dup_bits()
    assert still_guard.same_picture(first, second)


@pytest.mark.parametrize("size", S23B_A_SOURCES)
def test_orb_matches_a_side_window_against_the_padded_copy(size, tmp_path: Path) -> None:
    """A left or right window has another core hash. ORB still finds it."""
    pytest.importorskip("cv2")
    source = _photo(size, seed=7 + size[0])
    window, _how = prepare_reel_frame(source, "left")
    padded = _blur_fill(source)
    first = still_guard.traits(_save(window, tmp_path / "left.jpg"))
    second = still_guard.traits(_save(padded, tmp_path / "padded.jpg"))
    assert first is not None and second is not None
    assert still_guard.crop_match(first, second)


def test_different_photographs_stay_different(tmp_path: Path) -> None:
    paths = []
    for seed, size in enumerate(S23B_A_SOURCES + [(960, 700)]):
        frame, _how = prepare_reel_frame(_photo(size, seed=1000 + seed), "center")
        paths.append(_save(frame, tmp_path / f"p{seed}.jpg"))
        paths.append(_save(_blur_fill(_photo(size, seed=2000 + seed)), tmp_path / f"q{seed}.jpg"))
    assert still_guard.repeats(paths) == []
