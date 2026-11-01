"""Film titles for a render: a cold-open title card under a 2.39:1 matte, act chapter cards
with a dip between acts, and an end card.

Timing comes from the script (``render.py`` leaves ``TITLE_S`` of silence before the first line,
``ACT_GAP_S`` more between acts and ``END_S`` after the last line), so titles never sit on top of a
caption or a voiced line. Everything here is drawn away from the faces: title and end card in the
lower band (where captions go once the voices start), chapter cards in the top-left corner.
Serif italics (EB Garamond) for the topic, tracked Montserrat caps for names and labels.
"""
from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont

TITLE_S = 1.8  # silent cold open before the first line (title card over the establishing wide)
ACT_GAP_S = 0.7  # extra silence before the first line of a new act (the dip and the chapter card)
END_S = 2.4  # end card after the last line, then the fade-out
MATTE = 2.39  # scope aspect of the letterbox during titles
MATTE_EASE_S = 0.8  # bars slide off after the cold open and back for the end card
DIP_FLOOR = 0.32  # act dips go this dark, never to black
CHAPTER_HOLD_S = 2.8
ROMAN = ("I", "II", "III", "IV", "V", "VI")
ACT_LABEL = {
    "pt-BR": {"abertura": "Abertura", "confronto": "O confronto", "desenvolvimento": "Desenvolvimento",
              "virada": "A virada", "fechamento": "Fechamento"},
    "en-US": {"abertura": "Opening", "confronto": "The clash", "desenvolvimento": "Development",
              "virada": "The turn", "fechamento": "Closing"},
}
KIND = {"pt-BR": {"debate": "Um debate", "lesson": "Uma aula", "solo": "Um monólogo"},
        "en-US": {"debate": "A debate", "lesson": "A lesson", "solo": "A monologue"}}
_FONTS = Path(__file__).resolve().parents[2] / "Fonts"


@lru_cache(maxsize=None)
def font(name: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = _FONTS / "Lora" / "EBGaramond-Italic.ttf" if name == "serif" else _FONTS / "Montserrat" / "static" / f"Montserrat-{name}.ttf"
    try:
        return ImageFont.truetype(str(path), size)
    except OSError:
        return ImageFont.load_default()


def ease(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def tracked_width(d: ImageDraw.ImageDraw, text: str, f: Any, track: float) -> float:
    return sum(d.textlength(c, font=f) + track for c in text) - (track if text else 0)


def tracked(d: ImageDraw.ImageDraw, x: float, y: float, text: str, f: Any, fill: tuple, track: float) -> None:
    for c in text:
        d.text((x, y), c, font=f, fill=fill)
        x += d.textlength(c, font=f) + track


def acts(script: list[dict[str, Any]]) -> list[tuple[int, str]]:
    """(line index, act) for every line that opens a new act after the first."""
    out, cur = [], None
    for i, line in enumerate(script):
        act = line.get("act")
        if act and act != cur:
            if cur is not None:
                out.append((i, act))
            cur = act
    return out


def matte_h(w: int, h: int) -> int:
    """Bar height for a 2.39:1 window; 0 for vertical frames (the window would be a sliver)."""
    return max(0, round((h - w / MATTE) / 2)) if w > h else 0


def title_card(size: tuple[int, int], k: float, kicker: str, names: str, topic: str, accent: tuple[int, int, int],
               y_band: float) -> Image.Image:
    """Centred title block: tracked kicker in the accent colour, a hairline, tracked names and the topic in a
    large serif italic. ``y_band`` is the block's vertical centre (fraction of the frame height)."""
    w, h = size
    d = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    fk, fn, ft = font("SemiBold", round(15 * k)), font("Bold", round(30 * k)), font("serif", round(64 * k))
    tk, tn = 6.0 * k, 7.0 * k
    topic = topic[:1].upper() + topic[1:] if topic else ""
    while topic and d.textlength(topic, font=ft) > w * 0.8:
        ft = font("serif", ft.size - 2)
    rows = []  # row heights
    if kicker:
        rows.append(round(22 * k))
    if names:
        rows.append(round(44 * k))
    if topic:
        rows.append(round(ft.size * 1.15))
    gap = round(12 * k)
    total = sum(rows) + gap * (len(rows) - 1)
    y = round(h * y_band - total / 2)
    plate = Image.new("L", size, 0)
    pd = ImageDraw.Draw(plate)
    bw = max([d.textlength(topic, font=ft)] + [tracked_width(d, names, fn, tn)]) / 2 + 70 * k
    pd.rounded_rectangle((w / 2 - bw, y - 26 * k, w / 2 + bw, y + total + 26 * k), round(40 * k), fill=150)
    img = Image.new("RGBA", size, (4, 4, 8, 0))
    img.putalpha(plate.filter(ImageFilter.GaussianBlur(round(30 * k))))
    d = ImageDraw.Draw(img)
    shadow = Image.new("RGBA", size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    marks: list[tuple[str, float, float, Any, tuple, float]] = []
    if kicker:
        kw = tracked_width(d, kicker, fk, tk)
        marks.append((kicker, w / 2 - kw / 2, y, fk, (*accent, 255), tk))
        y += rows.pop(0) + gap
    if names:
        nw = tracked_width(d, names, fn, tn)
        marks.append((names, w / 2 - nw / 2, y, fn, (255, 255, 255, 250), tn))
        y += rows.pop(0) + gap
        d.line((w / 2 - 28 * k, y - gap / 2, w / 2 + 28 * k, y - gap / 2), fill=(*accent, 220), width=max(1, round(2 * k)))
    if topic:
        tw = d.textlength(topic, font=ft)
        marks.append((topic, w / 2 - tw / 2, y - round(ft.size * 0.12), ft, (255, 248, 236, 255), 0.0))
    for text, x, yy, f, _fill, tr in marks:
        tracked(sd, x, yy + 2 * k, text, f, (0, 0, 0, 200), tr)
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(round(4 * k))))
    d = ImageDraw.Draw(img)
    for text, x, yy, f, fill, tr in marks:
        tracked(d, x, yy, text, f, fill, tr)
    return img


def chapter_card(k: float, numeral: str, label: str, accent: tuple[int, int, int]) -> Image.Image:
    """Top-left chapter mark: a serif-italic roman numeral, a short accent rule and the tracked act name."""
    fn, fl = font("serif", round(40 * k)), font("SemiBold", round(14 * k))
    d = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    label = label.upper()
    nw, lw = d.textlength(numeral, font=fn), tracked_width(d, label, fl, 4.2 * k)
    w, h = round(nw + lw + 110 * k), round(64 * k)
    plate = Image.new("L", (w, h), 0)
    ImageDraw.Draw(plate).rounded_rectangle((6 * k, 6 * k, w - 6 * k, h - 6 * k), round(16 * k), fill=150)
    img = Image.new("RGBA", (w, h), (6, 6, 10, 0))
    img.putalpha(plate.filter(ImageFilter.GaussianBlur(round(8 * k))))
    d = ImageDraw.Draw(img)
    x = 24 * k
    d.text((x, h / 2 + 2 * k), numeral, font=fn, fill=(255, 248, 236, 255), anchor="lm")
    x += nw + 16 * k
    d.line((x, h / 2, x + 26 * k, h / 2), fill=(*accent, 230), width=max(1, round(2 * k)))
    tracked(d, x + 40 * k, h / 2 - fl.size * 0.62, label, fl, (*accent, 245), 4.2 * k)
    return img


def dip(t: float, windows: list[tuple[float, float]]) -> float:
    """Exposure for an act break: a smooth dip to ``DIP_FLOOR`` over each (start, end) silent window."""
    level = 1.0
    for a, b in windows:
        if a < t < b:
            u = (t - a) / max(1e-3, b - a)
            level = min(level, 1 - (1 - DIP_FLOOR) * math.sin(math.pi * u) ** 2)
    return level
