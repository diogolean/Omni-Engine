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
from .animator_types import SpeakerStyle


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


def _wrap_title(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    width: int,
    max_lines: int | None = 2,
) -> list[str]:
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
    if max_lines is not None:
        lines = lines[:max_lines]
    return lines or ["AI VS AI"]


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
    """Render a versus card using the seated production rigs.

    The default card is 16:9. Pass ``size=(1080, 1920)`` for a 9:16 Short.
    """
    if len(styles) != 2:
        raise ValueError("thumbnail generation requires exactly two fighters")
    width, height = size
    portrait = height > width
    canvas = _background(background_path, size).convert("RGBA")
    beams = Image.new("RGBA", size, (0, 0, 0, 0))
    beam_draw = ImageDraw.Draw(beams, "RGBA")
    beam_draw.polygon(
        ((0, 0), (int(width * 0.40), 0), (int(width * 0.59), height), (int(width * 0.26), height)),
        fill=(255, 188, 62, 35),
    )
    beam_draw.polygon(
        ((width, 0), (int(width * 0.64), 0), (int(width * 0.42), height), (int(width * 0.76), height)),
        fill=(48, 213, 255, 28),
    )
    canvas.alpha_composite(beams.filter(ImageFilter.GaussianBlur(28)))

    ordered = sorted(styles, key=lambda style: 0 if style.facing.lower() == "right" else 1)
    fighters = []
    # The aggressor is whoever speaks first, on whichever side it sits.
    aggressor = next((index for index, style in enumerate(ordered) if style.opens), 0)
    fighter_max = (int(width * 0.46), int(height * 0.42)) if portrait else (820, 900)
    for index, style in enumerate(ordered):
        rig = PuppetRig(PuppetSkin.load(Path(puppets_dir) / style.character_id))
        sprite = _fighter(
            rig,
            emotion="deboche" if index == aggressor else "shock_perplexed",
            max_size=fighter_max,
        )
        fighters.append(_aura(sprite, (23, 215, 255) if index == aggressor else (255, 173, 47)))
    for index, sprite in enumerate(fighters):
        x = 16 if index == 0 else width - sprite.width - 16
        y = height - sprite.height + (80 if portrait else 35)
        canvas.alpha_composite(sprite, (x, y))

    draw = ImageDraw.Draw(canvas, "RGBA")
    vs_font = _font(160 if portrait else 220)
    vs = "VS"
    box = draw.textbbox((0, 0), vs, font=vs_font, stroke_width=10)
    vx = (width - (box[2] - box[0])) // 2
    vy = int(height * 0.34) if portrait else 385
    draw.text((vx + 14, vy + 18), vs, font=vs_font, fill=(0, 0, 0, 210), stroke_width=12, stroke_fill=(0, 0, 0, 220))
    draw.text((vx, vy), vs, font=vs_font, fill=(238, 185, 62, 255), stroke_width=8, stroke_fill=(75, 39, 5, 255))

    rng = random.Random(seed)
    for _ in range(34):
        angle = rng.random() * math.tau
        radius = rng.randint(110, 260)
        x = width // 2 + int(math.cos(angle) * radius * (0.55 if portrait else 1))
        y = vy + 80 + int(math.sin(angle) * radius * 0.55)
        draw.line((x, y, x + rng.randint(-24, 24), y + rng.randint(-24, 24)), fill=(255, 197, 65, 190), width=4)

    # Shrink the type until the whole title fits on two lines. Cutting the
    # third line would end a question mid-sentence.
    title_text = title or "AI VS AI"
    title_width = width - (80 if portrait else 280)
    title_sizes = (72, 64, 56, 48, 42) if portrait else (112, 98, 86, 76, 68)
    for title_size in title_sizes:
        title_font = _font(title_size)
        if len(_wrap_title(draw, title_text, title_font, title_width, max_lines=None)) <= 2:
            break
    lines = _wrap_title(draw, title_text, title_font, title_width)
    line_height = round((92 if portrait else 126) * title_size / title_sizes[0])
    banner_h = 48 + line_height * len(lines)
    margin = 36 if portrait else 90
    draw.rounded_rectangle(
        (margin, 54, width - margin, 54 + banner_h),
        radius=28,
        fill=(0, 0, 0, 205),
        outline=(238, 185, 62, 230),
        width=6,
    )
    for index, line in enumerate(lines):
        text_width = draw.textlength(line, font=title_font)
        draw.text(
            ((width - text_width) / 2, 75 + index * line_height),
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
