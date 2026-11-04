"""Procedural Einstein bust for the product path. No image call.

The plate is a shaded head: warm skin, white hair, a moustache, and a blank
mouth. Drawn lips composite on that skin. ``mouth_fit`` then paints the soft
fringe with the cheek under it, so the skin gate measures a real match.
Landmark boxes stay the illustrated face so the nose–chin anchor still lands
under the moustache.
"""
from __future__ import annotations

from typing import Any, Sequence

import numpy as np
from PIL import Image, ImageDraw

CANVAS = (640, 800)
FACE = [190, 78, 450, 372]
SKIN = (214, 166, 134)
HAIR = (232, 228, 220, 255)
HAIR_MID = (206, 202, 194, 255)
HAIR_SHADOW = (168, 164, 156, 255)
LIP = {
    "outline": (92, 48, 44, 255),
    "cavity": (48, 24, 26, 255),
    "teeth": (246, 240, 232, 255),
    "tongue": (176, 84, 86, 255),
    "lip": (158, 86, 80, 255),
}


def _shade_head(canvas: np.ndarray) -> None:
    """Warm head, a soft nose, and shoulders. The mouth band stays plain skin."""
    height, width = canvas.shape[:2]
    x0, y0, x1, y1 = FACE
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    rx, ry = (x1 - x0) / 2, (y1 - y0) / 2
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    nx = (xx - cx) / rx
    ny = (yy - cy) / ry
    inside = nx * nx + ny * ny <= 1.0
    z = np.sqrt(np.clip(1.0 - np.clip(nx * nx + ny * ny, 0, 1), 0, 1))
    light = np.clip(nx * -0.28 + ny * -0.48 + z * 0.82, 0, 1)
    shadow = np.array([132, 90, 72], np.float32)
    base = np.array(SKIN, np.float32)
    lit = np.array([236, 198, 168], np.float32)
    tone = shadow + (base - shadow) * light[..., None]
    peak = np.clip((light - 0.58) / 0.42, 0, 1)[..., None]
    tone = tone + (lit - tone) * peak
    cheek = np.exp(-(((xx - 262) / 34) ** 2 + ((yy - 246) / 28) ** 2))
    cheek = cheek + np.exp(-(((xx - 378) / 34) ** 2 + ((yy - 246) / 28) ** 2))
    blush = np.array([176, 104, 96], np.float32)
    cheek_w = np.clip(cheek, 0, 1)[..., None]
    tone = tone * (1 - 0.22 * cheek_w) + blush * (0.22 * cheek_w)
    chin = np.clip((ny - 0.42) / 0.5, 0, 1)[..., None]
    tone = tone * (1 - 0.18 * chin) + shadow * (0.18 * chin)
    # Nose: a narrow lit ridge and two soft nostrils, above the moustache.
    ridge = np.exp(-((xx - 320) / 16) ** 2 - ((yy - 250) / 48) ** 2)
    tone = tone + np.array([18, 12, 8], np.float32) * ridge[..., None]
    for nostril_x in (304, 336):
        well = np.exp(-((xx - nostril_x) / 7) ** 2 - ((yy - 294) / 5) ** 2)
        tone = tone * (1 - 0.55 * well[..., None]) + np.array([96, 58, 52], np.float32) * (0.55 * well[..., None])
    grain = np.random.default_rng(4).normal(0, 2.2, tone.shape)
    tone = np.clip(tone + grain, 0, 255)
    canvas[inside, :3] = tone[inside]
    canvas[inside, 3] = 255
    # Neck into shoulders that stay inside the jacket, then the same suit colour.
    half = np.clip(36 + np.clip(yy - 350, 0, None) * 0.72, 36, 145)
    neck = (np.abs(xx - 320) < half) & (yy >= 340) & (yy <= 500)
    suit = (np.abs(xx - 320) < 168) & (yy >= 488) & (yy <= 680)
    shirt = (np.abs(xx - 320) < 28 + np.clip(yy - 450, 0, None) * 0.15) & (yy >= 445) & (yy <= 560)
    body = neck | suit
    shade = np.clip((xx - 200) / 240, 0, 1)
    dark = np.array([150, 104, 84], np.float32)
    mid = np.array([198, 148, 120], np.float32)
    skin = dark + (mid - dark) * shade[..., None]
    canvas[body, :3] = skin[body]
    canvas[body, 3] = 255
    canvas[suit, :3] = (28, 30, 36)
    canvas[suit, 3] = 255
    canvas[shirt, :3] = (236, 234, 228)
    canvas[shirt, 3] = 255
    # The face oval wins where it overlaps the neck.
    canvas[inside, :3] = tone[inside]
    canvas[inside, 3] = 255


def _curve(
    start: tuple[float, float],
    end: tuple[float, float],
    bend: tuple[float, float],
    steps: int = 9,
) -> list[tuple[float, float]]:
    points = []
    for step in range(steps):
        t = step / (steps - 1)
        x = (1 - t) ** 2 * start[0] + 2 * (1 - t) * t * bend[0] + t ** 2 * end[0]
        y = (1 - t) ** 2 * start[1] + 2 * (1 - t) * t * bend[1] + t ** 2 * end[1]
        points.append((x, y))
    return points


def _hair(draw: ImageDraw.ImageDraw) -> None:
    """Strands off the scalp. The brow, the eyes, and the mouth stay clear."""
    rng = np.random.default_rng(11)
    for index in range(72):
        frac = index / 71
        # Upper fan only. Strands that point down land on the eyes.
        angle = -np.pi * (0.08 + 0.84 * frac)
        length = 34 + int(rng.integers(0, 26))
        if abs(np.cos(angle)) > 0.72:
            length += 18
        origin_x = 320 + np.cos(angle) * (78 + rng.random() * 18)
        origin_y = 118 + np.sin(angle) * (28 + rng.random() * 10)
        end_x = origin_x + np.cos(angle) * length
        end_y = origin_y + np.sin(angle) * length * 0.85 - 8
        bend = (
            (origin_x + end_x) / 2 + float(rng.normal(0, 10)),
            (origin_y + end_y) / 2 + float(rng.normal(0, 6)),
        )
        colour = HAIR if index % 3 else HAIR_MID if index % 2 else HAIR_SHADOW
        width = 3 if index % 4 else 4
        draw.line(_curve((origin_x, origin_y), (end_x, end_y), bend), fill=colour, width=width, joint="curve")
    # Side locks, outside the eyes.
    for side in (-1, 1):
        for index in range(8):
            x0 = 320 + side * (132 + index * 3)
            y0 = 150 + index * 8
            end = (x0 + side * 16, y0 + 70 + index * 4)
            bend = (x0 + side * 28, y0 + 30)
            draw.line(_curve((x0, y0), end, bend), fill=HAIR if index % 2 == 0 else HAIR_SHADOW, width=4, joint="curve")


def _moustache(draw: ImageDraw.ImageDraw) -> None:
    """Short pale strokes under the nose. Nothing crosses the blank mouth."""
    rng = np.random.default_rng(3)
    for index in range(18):
        x = 258 + index * 7
        y = 304 + (4 if index % 2 else 0)
        end_y = 314 + int(rng.integers(0, 4))
        colour = HAIR if index % 3 else HAIR_MID
        draw.line(_curve((x, y), (x + 10, end_y), (x + 4, y - 2)), fill=colour, width=3, joint="curve")
    draw.polygon(
        [(262, 308), (292, 302), (320, 306), (348, 302), (378, 308), (368, 316), (320, 314), (272, 316)],
        fill=(228, 224, 216, 255),
    )


def _forehead(draw: ImageDraw.ImageDraw) -> None:
    tone = (156, 110, 92, 140)
    for y, x0, x1 in ((132, 256, 384), (146, 268, 372)):
        draw.arc((x0, y, x1, y + 14), 200, 340, fill=tone, width=1)


def paint(
    eyes: Sequence[dict[str, Any]],
    brows: Sequence[Sequence[tuple[int, int]]],
) -> Image.Image:
    """Shaded bust. ``eyes`` and ``brows`` are the contours the cutter will lift."""
    canvas = np.zeros((CANVAS[1], CANVAS[0], 4), np.float32)
    _shade_head(canvas)
    image = Image.fromarray(np.clip(canvas, 0, 255).astype(np.uint8), "RGBA")
    draw = ImageDraw.Draw(image)
    for cx, cy in ((172, 240), (468, 240)):
        draw.ellipse((cx - 18, cy - 28, cx + 18, cy + 32), fill=(190, 140, 114, 255))
        draw.ellipse((cx - 8, cy - 12, cx + 10, cy + 16), fill=(158, 108, 90, 255))
    for eye in eyes:
        upper, lower = eye["upper"], eye["lower"]
        draw.polygon(list(upper) + list(lower)[::-1], fill=(244, 240, 234, 255))
        # A soft lid crease just above the opening.
        draw.line(list(upper), fill=(120, 78, 68, 180), width=2)
        iris_x, iris_y = eye["iris"]
        radius = int(eye["radius"])
        draw.ellipse(
            (iris_x - radius, iris_y - radius, iris_x + radius, iris_y + radius),
            fill=(58, 72, 54, 255),
        )
        pupil = max(2, radius // 2)
        draw.ellipse(
            (iris_x - pupil, iris_y - pupil + 1, iris_x + pupil, iris_y + pupil + 1),
            fill=(16, 12, 10, 255),
        )
        draw.ellipse((iris_x - radius + 2, iris_y - radius + 2, iris_x - 1, iris_y - 1), fill=(250, 248, 242, 220))
    _moustache(draw)
    _hair(draw)
    _forehead(draw)
    for poly in brows:
        draw.polygon(list(poly), fill=(72, 64, 58, 255))
    return image
