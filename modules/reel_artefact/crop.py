# -*- coding: utf-8 -*-
"""9:16 reel frame. Smart crop when the photo is already portrait."""
from __future__ import annotations

from PIL import Image, ImageFilter

REEL_W = 1080
REEL_H = 1920
# Long side at or above this is used as-is (then fit to 9:16).
STILL_DIRECT_LONG = 1080
# Long side from here up to the direct floor is upscaled locally.
UPSCALE_MIN_LONG = 720
# Kept so older logs and metadata still name the floor.
STILL_MIN_SHORT = UPSCALE_MIN_LONG
REFERENCE_MIN_SHORT = 200
_ANCHORS = ("center", "left", "right", "upper", "lower")


def anchor_for(index: int) -> str:
    """Crop window for beat ``index``. Successive beats do not repeat the same edge."""
    return _ANCHORS[int(index) % len(_ANCHORS)]


def prepare_reel_frame(img: Image.Image, anchor: str = "center") -> tuple[Image.Image, str]:
    """Return a 1080×1920 RGB frame and how it was fit.

    A centered near-portrait photo is center-cropped to 9:16. A centered
    landscape stays whole on a blurred cover. Other anchors slide a 9:16
    window so the same photograph can serve another beat. A source whose
    long side is under 1080px and at least 720px is upscaled with Lanczos
    and a light sharpen. No paid upscaler.
    """
    image = img.convert("RGB")
    width, height = image.size
    if width < 2 or height < 2:
        raise ValueError("image_too_small")
    source_long = max(width, height)
    choice = anchor if anchor in _ANCHORS else "center"
    ratio = width / height
    if choice == "center" and 0.45 <= ratio <= 0.70:
        frame, how = _smart_crop(image), "smart_crop"
    elif choice == "center":
        frame, how = _blur_fill(image), "blur_fill"
    else:
        frame, how = _window(image, choice), "window"
    if UPSCALE_MIN_LONG <= source_long < STILL_DIRECT_LONG:
        frame = frame.filter(ImageFilter.UnsharpMask(radius=1.5, percent=70, threshold=3))
        how = "upscale_" + how
    return frame, how


def _smart_crop(image: Image.Image) -> Image.Image:
    width, height = image.size
    target_w = int(round(height * 9 / 16))
    target_w = max(1, min(width, target_w))
    if target_w < width:
        left = max(0, (width - target_w) // 2)
        image = image.crop((left, 0, left + target_w, height))
    return image.resize((REEL_W, REEL_H), Image.Resampling.LANCZOS)


def _window(image: Image.Image, anchor: str) -> Image.Image:
    """Slide a 9:16 window. Left and right move across a wide photo."""
    width, height = image.size
    target_ratio = 9 / 16
    if width / height > target_ratio:
        crop_w = max(1, int(round(height * target_ratio)))
        crop_w = min(width, crop_w)
        if anchor == "left":
            left = 0
        elif anchor == "right":
            left = width - crop_w
        else:
            left = max(0, (width - crop_w) // 2)
        image = image.crop((left, 0, left + crop_w, height))
    else:
        crop_h = max(1, int(round(width / target_ratio)))
        crop_h = min(height, crop_h)
        if anchor == "upper":
            top = 0
        elif anchor == "lower":
            top = max(0, height - crop_h)
        else:
            top = max(0, (height - crop_h) // 2)
        image = image.crop((0, top, width, top + crop_h))
    return image.resize((REEL_W, REEL_H), Image.Resampling.LANCZOS)


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
    """True when the long side can be a still, including a local upscale."""
    if width <= 0 or height <= 0:
        return False
    return max(width, height) >= UPSCALE_MIN_LONG


def usable_as_reference(width: int, height: int) -> bool:
    if width <= 0 or height <= 0:
        return False
    return min(width, height) >= REFERENCE_MIN_SHORT
