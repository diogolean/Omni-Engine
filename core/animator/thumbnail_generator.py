# -*- coding: utf-8 -*-
"""Deterministic fighting-game thumbnail renderer for dialogue puppets."""
from __future__ import annotations

import math
import random
from pathlib import Path
from typing import Sequence

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from .puppet import PuppetRig, PuppetSkin
from .types import SpeakerStyle


def _font(size: int) -> ImageFont.ImageFont:
    for candidate in (
        Path(r"C:\Windows\Fonts\impact.ttf"),
        Path(r"C:\Windows\Fonts\arialbd.ttf"),
        Path(r"C:\Windows\Fonts\seguisb.ttf"),
    ):
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


def _background(path: Path | None, size: tuple[int, int]) -> Image.Image:
    if path is not None and path.is_file():
        with Image.open(path) as opened:
            image = ImageOps.fit(
                opened.convert("RGB"),
                size,
                method=Image.Resampling.LANCZOS,
            )
    else:
        image = Image.new("RGB", size, (18, 14, 10))
    image = ImageEnhance.Contrast(image).enhance(1.35)
    image = ImageEnhance.Brightness(image).enhance(0.48)
    vignette = Image.new("L", size, 0)
    ImageDraw.Draw(vignette).ellipse(
        (-size[0] // 5, -size[1] // 3, size[0] * 6 // 5, size[1] * 4 // 3),
        fill=235,
    )
    vignette = vignette.filter(ImageFilter.GaussianBlur(150))
    return Image.composite(image, Image.new("RGB", size, (2, 2, 3)), vignette)


def _fighter(rig: PuppetRig, *, emotion: str, max_size: tuple[int, int]) -> Image.Image:
    frame = rig.compose(viseme="X", emotion=emotion)
    rgba = Image.fromarray(np.asarray(frame, dtype=np.uint8), mode="RGBA")
    alpha = np.asarray(rgba)[..., 3]
    ys, xs = np.nonzero(alpha > 8)
    if not xs.size:
        return rgba
    bust_bottom = min(rgba.height, int(ys.min() + (ys.max() - ys.min() + 1) * 0.83))
    crop = rgba.crop((int(xs.min()), int(ys.min()), int(xs.max()) + 1, bust_bottom))
    crop.thumbnail(max_size, Image.Resampling.LANCZOS)
    return crop


def _aura(sprite: Image.Image, color: tuple[int, int, int]) -> Image.Image:
    alpha = sprite.getchannel("A")
    glow = Image.new("RGBA", sprite.size, (*color, 0))
    glow.putalpha(alpha.filter(ImageFilter.GaussianBlur(24)).point(lambda value: min(170, value)))
    plate = Image.new("RGBA", sprite.size, (0, 0, 0, 0))
    plate.alpha_composite(glow)
    plate.alpha_composite(sprite)
    return plate


def _wrap_title(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, width: int) -> list[str]:
    words = text.upper().split()
    lines: list[str] = []
    current = ""
    for word in words:
        trial = word if not current else f"{current} {word}"
        if draw.textlength(trial, font=font) <= width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines[:2] or ["AI VS AI"]


def generate_vs_thumbnail(
    *,
    title: str,
    styles: Sequence[SpeakerStyle],
    puppets_dir: Path,
    destination: Path,
    background_path: Path | None = None,
    size: tuple[int, int] = (1920, 1080),
    seed: int = 0,
) -> Path:
    """Render a 1920x1080 versus card using the seated production rigs."""
    if len(styles) != 2:
        raise ValueError("thumbnail generation requires exactly two fighters")
    canvas = _background(background_path, size).convert("RGBA")
    beams = Image.new("RGBA", size, (0, 0, 0, 0))
    beam_draw = ImageDraw.Draw(beams, "RGBA")
    beam_draw.polygon(((0, 0), (760, 0), (1140, 1080), (500, 1080)), fill=(255, 188, 62, 35))
    beam_draw.polygon(((1920, 0), (1220, 0), (800, 1080), (1450, 1080)), fill=(48, 213, 255, 28))
    canvas.alpha_composite(beams.filter(ImageFilter.GaussianBlur(28)))

    ordered = sorted(styles, key=lambda style: 0 if style.facing.lower() == "right" else 1)
    fighters = []
    for index, style in enumerate(ordered):
        rig = PuppetRig(PuppetSkin.load(Path(puppets_dir) / style.character_id))
        sprite = _fighter(
            rig,
            emotion="deboche" if index == 0 else "shock_perplexed",
            max_size=(820, 900),
        )
        fighters.append(_aura(sprite, (23, 215, 255) if index == 0 else (255, 173, 47)))
    for index, sprite in enumerate(fighters):
        x = 32 if index == 0 else size[0] - sprite.width - 32
        y = size[1] - sprite.height + 35
        canvas.alpha_composite(sprite, (x, y))

    draw = ImageDraw.Draw(canvas, "RGBA")
    vs_font = _font(220)
    vs = "VS"
    box = draw.textbbox((0, 0), vs, font=vs_font, stroke_width=10)
    vx = (size[0] - (box[2] - box[0])) // 2
    vy = 385
    draw.text((vx + 14, vy + 18), vs, font=vs_font, fill=(0, 0, 0, 210), stroke_width=12, stroke_fill=(0, 0, 0, 220))
    draw.text((vx, vy), vs, font=vs_font, fill=(238, 185, 62, 255), stroke_width=8, stroke_fill=(75, 39, 5, 255))

    rng = random.Random(seed)
    for _ in range(34):
        angle = rng.random() * math.tau
        radius = rng.randint(110, 260)
        x = size[0] // 2 + int(math.cos(angle) * radius)
        y = 500 + int(math.sin(angle) * radius * 0.55)
        draw.line((x, y, x + rng.randint(-24, 24), y + rng.randint(-24, 24)), fill=(255, 197, 65, 190), width=4)

    title_font = _font(112)
    lines = _wrap_title(draw, title or "AI VS AI", title_font, 1640)
    line_height = 126
    banner_h = 48 + line_height * len(lines)
    draw.rounded_rectangle(
        (90, 54, size[0] - 90, 54 + banner_h),
        radius=28,
        fill=(0, 0, 0, 205),
        outline=(238, 185, 62, 230),
        width=6,
    )
    for index, line in enumerate(lines):
        width = draw.textlength(line, font=title_font)
        draw.text(
            ((size[0] - width) / 2, 75 + index * line_height),
            line,
            font=title_font,
            fill=(255, 246, 220, 255) if index == 0 else (255, 201, 55, 255),
            stroke_width=8,
            stroke_fill=(0, 0, 0, 255),
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(destination, "PNG", optimize=True)
    return destination


__all__ = ["generate_vs_thumbnail"]
