"""Generated stand-in puppet. Nothing here is a shipped bitmap.

The pictures are painted when a scene names a character without a puppet
directory. Mouths differ by viseme so lip-sync is visible on the stand-in.
A product asset provider replaces this for a real show.
"""
from __future__ import annotations

from PIL import Image, ImageDraw

_INK = (22, 26, 32, 255)
_WHITE = (236, 240, 244, 255)
_BG = (18, 22, 28, 255)


def paint_placeholder_layers(skin, *, missing: list[str] | None = None) -> None:
    """Write any missing PNG layers for ``skin``."""
    skin.root.mkdir(parents=True, exist_ok=True)
    size = (
        int(skin.reference_canvas_size[0]),
        int(skin.reference_canvas_size[1]),
    )
    keys = list(missing) if missing is not None else []
    for key in keys:
        path = skin.layer_path(key)
        if path.is_file():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        _layer(key, skin, size).save(path, format="PNG", compress_level=1)


def _layer(key: str, skin, size: tuple[int, int]) -> Image.Image:
    if key == "bg":
        image = Image.new("RGBA", size, _BG)
        return image
    if key == "body":
        return _body(skin, size)
    if key == "head":
        return _head(skin, size)
    if key == "glow":
        return _glow(skin, size)
    if key == "eyes_open":
        return _eyes(skin, size, "open")
    if key == "eyes_half":
        return _eyes(skin, size, "half")
    if key == "eyes_blink":
        return _eyes(skin, size, "blink")
    if key.startswith("mouth_"):
        return _mouth(skin, size, key)
    return Image.new("RGBA", size, (0, 0, 0, 0))


def _theme(skin) -> tuple[int, int, int]:
    raw = ""
    theme = getattr(skin, "theme", None)
    if theme is not None:
        raw = str(getattr(theme, "glow_color", "") or "")
    return _hex(raw, (90, 122, 138))


def _hex(value: str, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
    text = value.strip().lstrip("#")
    if len(text) != 6:
        return fallback
    try:
        return (
            int(text[0:2], 16),
            int(text[2:4], 16),
            int(text[4:6], 16),
        )
    except ValueError:
        return fallback


def _with_alpha(color: tuple[int, int, int], alpha: int = 255) -> tuple[int, int, int, int]:
    return (color[0], color[1], color[2], alpha)


def _body(skin, size: tuple[int, int]) -> Image.Image:
    width, height = size
    color = _with_alpha(_theme(skin))
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.polygon(
        [
            (int(width * 0.30), int(height * 0.50)),
            (int(width * 0.70), int(height * 0.50)),
            (int(width * 0.86), int(height * 0.98)),
            (int(width * 0.14), int(height * 0.98)),
        ],
        fill=color,
    )
    draw.rectangle(
        (int(width * 0.40), int(height * 0.44), int(width * 0.60), int(height * 0.62)),
        fill=color,
    )
    return image


def _head(skin, size: tuple[int, int]) -> Image.Image:
    del skin
    width, height = size
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    # A warm face reads on the dark stage. The body keeps the accent color.
    draw.ellipse(
        (int(width * 0.22), int(height * 0.06), int(width * 0.78), int(height * 0.52)),
        fill=(232, 214, 198, 255),
        outline=(186, 164, 148, 255),
        width=max(2, width // 80),
    )
    return image


def _glow(skin, size: tuple[int, int]) -> Image.Image:
    width, height = size
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse(
        (int(width * 0.16), int(height * 0.04), int(width * 0.84), int(height * 0.62)),
        fill=_with_alpha(_theme(skin), 80),
    )
    return image


def _eyes(skin, size: tuple[int, int], kind: str) -> Image.Image:
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    radius = max(4, int(skin.anchors.eye_radius))
    lid = (232, 214, 198, 255)
    for center in (skin.anchors.left_eye, skin.anchors.right_eye):
        x, y = int(center[0]), int(center[1])
        box = (x - radius, y - radius, x + radius, y + radius)
        if kind == "open":
            draw.ellipse(box, fill=_WHITE)
            pupil = max(2, radius // 3)
            # Shifted off center so a left-facing flip is a different picture.
            nudge = max(1, radius // 8)
            draw.ellipse(
                (x - pupil + nudge, y - pupil, x + pupil + nudge, y + pupil),
                fill=_INK,
            )
            continue
        draw.ellipse(box, fill=lid)
        if kind == "half":
            slit = max(2, radius // 4)
            draw.ellipse((x - radius, y - slit, x + radius, y + slit), fill=_WHITE)
            continue
        draw.line(
            (x - radius, y, x + radius, y),
            fill=_INK,
            width=max(2, radius // 6),
        )
    return image


_MOUTH_OPEN = {
    "A": (0.36, 0.18),
    "B": (0.48, 0.16),
    "C": (0.58, 0.46),
    "D": (0.86, 0.62),
    "E": (0.42, 0.52),
    "F": (0.32, 0.50),
    "G": (0.52, 0.24),
    "H": (0.66, 0.32),
    "X": (0.50, 0.10),
}


def _mouth(skin, size: tuple[int, int], key: str) -> Image.Image:
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    radius = max(4, int(skin.anchors.eye_radius))
    cx, cy = int(skin.anchors.mouth[0]), int(skin.anchors.mouth[1])
    token = key.removeprefix("mouth_")
    if token == "smug_smile":
        _arc(draw, cx, cy, radius, smile=True)
        return image
    if token == "stressed_grimace":
        _arc(draw, cx, cy, radius, smile=False)
        return image
    letter = (token[:1] or "X").upper()
    width_frac, height_frac = _MOUTH_OPEN.get(letter, _MOUTH_OPEN["X"])
    mouth_w = max(6, int(radius * 2 * width_frac))
    mouth_h = max(3, int(radius * 2 * height_frac))
    box = (cx - mouth_w // 2, cy - mouth_h // 2, cx + mouth_w // 2, cy + mouth_h // 2)
    if letter == "X":
        draw.line(
            (box[0], cy, box[2], cy),
            fill=_INK,
            width=max(3, radius // 8),
        )
        return image
    draw.ellipse(box, fill=_INK)
    if mouth_h > radius * 0.35:
        inset = max(2, mouth_h // 5)
        draw.ellipse(
            (box[0] + inset, box[1] + inset, box[2] - inset, box[3] - inset),
            fill=(12, 14, 18, 255),
        )
    return image


def _arc(draw: ImageDraw.ImageDraw, cx: int, cy: int, radius: int, *, smile: bool) -> None:
    span = int(radius * 1.3)
    box = (cx - span, cy - span // 2, cx + span, cy + span // 2)
    start, end = (15, 165) if smile else (200, 340)
    draw.arc(box, start=start, end=end, fill=_INK, width=max(3, radius // 7))
