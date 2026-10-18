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


def prepare_event_frame(img: Image.Image) -> tuple[Image.Image, str]:
    """9:16 crop around the people or the boat, then fit the reel frame.

    A centre crop of a wreck photograph keeps the empty sea and cuts off
    the divers. The window sits on the busiest, darkest part of the frame.
    """
    image = img.convert("RGB")
    width, height = image.size
    if width < 2 or height < 2:
        raise ValueError("image_too_small")
    left, top, right, bottom = _salient_box(image)
    cropped = image.crop((left, top, right, bottom))
    frame = cropped.resize((REEL_W, REEL_H), Image.Resampling.LANCZOS)
    return frame, "salient_crop"


def _salient_box(image: Image.Image) -> tuple[int, int, int, int]:
    """A 9:16 window that covers the high-contrast, darker subject."""
    width, height = image.size
    energy, ew, eh = _energy(image)
    target = 9 / 16
    if width / max(1, height) > target:
        crop_w = max(1, min(width, int(round(height * target))))
        crop_h = height
        span = max(0, width - crop_w)
        best = (0, 0, crop_w, height)
    else:
        crop_h = max(1, min(height, int(round(width / target))))
        crop_w = width
        span = max(0, height - crop_h)
        best = (0, 0, width, crop_h)
    best_score = -1.0
    steps = 32
    for step in range(steps + 1):
        if width / max(1, height) > target:
            left = int(round(span * step / steps)) if steps else 0
            top = 0
        else:
            left = 0
            top = int(round(span * step / steps)) if steps else 0
        score = _window_score(energy, ew, eh, width, height, left, top, crop_w, crop_h)
        if score > best_score:
            best_score = score
            best = (left, top, left + crop_w, top + crop_h)
    return best


def _energy(image: Image.Image) -> tuple[list[list[float]], int, int]:
    small = image.convert("RGB")
    small.thumbnail((96, 96), Image.Resampling.BOX)
    width, height = small.size
    pixels = list(small.getdata())

    def luma(index: int) -> float:
        red, green, blue = pixels[index]
        return 0.2126 * red + 0.7152 * green + 0.0722 * blue

    grid = [[0.0] * width for _ in range(height)]
    for y in range(height):
        for x in range(width):
            index = y * width + x
            red, green, blue = pixels[index]
            level = luma(index)
            contrast = 0.0
            seen = 0
            for dy, dx in ((0, 1), (1, 0), (0, -1), (-1, 0)):
                xx, yy = x + dx, y + dy
                if 0 <= xx < width and 0 <= yy < height:
                    contrast += abs(level - luma(yy * width + xx))
                    seen += 1
            contrast = contrast / max(1, seen)
            dark = max(0.0, 190.0 - level) / 190.0
            skin = 0.0
            if red > 90 and green > 40 and red > green > blue and (red - blue) > 15:
                skin = 1.0
            grid[y][x] = contrast + (55.0 * dark) + (90.0 * skin)
    return grid, width, height


def _window_score(
    energy: list[list[float]],
    ew: int,
    eh: int,
    width: int,
    height: int,
    left: int,
    top: int,
    crop_w: int,
    crop_h: int,
) -> float:
    x0 = int(left / max(1, width) * ew)
    x1 = max(x0 + 1, int((left + crop_w) / max(1, width) * ew))
    y0 = int(top / max(1, height) * eh)
    y1 = max(y0 + 1, int((top + crop_h) / max(1, height) * eh))
    x1 = min(ew, x1)
    y1 = min(eh, y1)
    total = 0.0
    count = 0
    for y in range(y0, y1):
        row = energy[y]
        for x in range(x0, x1):
            total += row[x]
            count += 1
    return total / max(1, count)


def usable_as_still(width: int, height: int) -> bool:
    """True when the long side can be a still, including a local upscale."""
    if width <= 0 or height <= 0:
        return False
    return max(width, height) >= UPSCALE_MIN_LONG


def usable_as_reference(width: int, height: int) -> bool:
    if width <= 0 or height <= 0:
        return False
    return min(width, height) >= REFERENCE_MIN_SHORT
