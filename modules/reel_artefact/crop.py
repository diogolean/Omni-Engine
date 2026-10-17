# -*- coding: utf-8 -*-
"""9:16 reel frame. Smart crop when the photo is already portrait."""
from __future__ import annotations

from PIL import Image, ImageFilter

REEL_W = 1080
REEL_H = 1920
# A still this small is a reference, not the frame we publish.
STILL_MIN_SHORT = 720
REFERENCE_MIN_SHORT = 200


def prepare_reel_frame(img: Image.Image) -> tuple[Image.Image, str]:
    """Return a 1080×1920 RGB frame and how it was fit.

    A near-portrait photo is center-cropped to 9:16. A landscape photo stays
    whole, centered on a blurred cover of itself, so the artefact is not cut
    off to fill the phone frame.
    """
    image = img.convert("RGB")
    width, height = image.size
    if width < 2 or height < 2:
        raise ValueError("image_too_small")
    ratio = width / height
    if 0.45 <= ratio <= 0.70:
        target_w = int(round(height * 9 / 16))
        target_w = max(1, min(width, target_w))
        if target_w < width:
            left = max(0, (width - target_w) // 2)
            image = image.crop((left, 0, left + target_w, height))
        frame = image.resize((REEL_W, REEL_H), Image.Resampling.LANCZOS)
        return frame, "smart_crop"
    return _blur_fill(image), "blur_fill"


def _blur_fill(image: Image.Image) -> Image.Image:
    width, height = image.size
    cover = max(REEL_W / width, REEL_H / height)
    bg_w = max(REEL_W, int(width * cover) + 1)
    bg_h = max(REEL_H, int(height * cover) + 1)
    background = image.resize((bg_w, bg_h), Image.Resampling.LANCZOS)
    left = max(0, (bg_w - REEL_W) // 2)
    top = max(0, (bg_h - REEL_H) // 2)
    background = background.crop((left, top, left + REEL_W, top + REEL_H))
    background = background.filter(ImageFilter.GaussianBlur(radius=28))
    fit = min(REEL_W / width, REEL_H / height)
    fg_w = max(1, int(width * fit))
    fg_h = max(1, int(height * fit))
    foreground = image.resize((fg_w, fg_h), Image.Resampling.LANCZOS)
    background.paste(foreground, ((REEL_W - fg_w) // 2, (REEL_H - fg_h) // 2))
    return background


def usable_as_still(width: int, height: int) -> bool:
    if width <= 0 or height <= 0:
        return False
    return min(width, height) >= STILL_MIN_SHORT


def usable_as_reference(width: int, height: int) -> bool:
    if width <= 0 or height <= 0:
        return False
    return min(width, height) >= REFERENCE_MIN_SHORT
