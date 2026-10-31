"""Rhubarb mouth set (X, A-H) as separate transparent PNGs over an always-mouthless head.

Reuses the Aiwake drawn mouth library (``products.aiwake.animator.asset_generator._draw_ghibli_mouth_patch``,
the organic hand-drawn Rhubarb shapes used by the Aiwake puppets) instead of cutting mouths out of
generated art: one shape per cue, same scale and one fixed anchor for all nine, tinted to the
avatar's own lip / skin tones. The renderer swaps exactly one PNG per frame (hard cut)."""
from __future__ import annotations

from typing import Any

import numpy as np
from PIL import Image

VISEMES = "XABCDEFGH"
_REF_PLATE = (400, 300)  # library units: patch 264 x 150, widest shape ~0.73 x patch width


def tones(lips_rgba: np.ndarray, skin: tuple[int, int, int]) -> dict[str, tuple[int, int, int, int]]:
    """Outline / cavity / teeth / tongue from the avatar's own closed lips (``lips_rgba``) and skin."""
    px = lips_rgba[lips_rgba[..., 3] > 160][:, :3].astype(np.float32)
    s = np.array(skin, np.float32)
    if len(px) > 20:
        red = px[(px[:, 0] - px[:, 1]) > (s[0] - s[1]) + 12]
        lip = np.median(red, axis=0) if len(red) > 10 else s * np.array([0.92, 0.72, 0.72])
    else:
        lip = s * np.array([0.92, 0.72, 0.72])
    outline = np.clip(lip * 0.45, 0, 255)
    cavity = np.clip(lip * np.array([0.28, 0.14, 0.15]), 0, 255)
    tongue = np.clip(lip * np.array([1.0, 0.78, 0.78]), 0, 255)
    teeth = np.array([246, 242, 236], np.float32)
    f = lambda c: tuple(int(x) for x in c) + (255,)  # noqa: E731
    return {"outline": f(outline), "cavity": f(cavity), "teeth": f(teeth), "tongue": f(tongue), "lip": f(lip)}


def draw_set(lip_width: float, colours: dict[str, tuple[int, int, int, int]], *, squeeze: float = 1.0,
             open_cap: float | dict[str, float] | None = None) -> dict[str, Image.Image]:
    """Nine RGBA mouths on one shared canvas (same size => same anchor). ``lip_width``: avatar mouth
    width in px; ``squeeze`` < 1 foreshortens 3/4 views; ``open_cap``: max drawn height in px."""
    from products.aiwake.animator.asset_generator import _draw_ghibli_mouth_patch  # noqa: PLC0415

    colours = dict(colours)
    lip = colours.pop("lip", None)
    raw = {v: _draw_ghibli_mouth_patch(v, plate_width=_REF_PLATE[0], plate_height=_REF_PLATE[1], organic=True, **colours)
           for v in VISEMES}
    pw = raw["X"].width
    k = lip_width / (0.73 * pw)
    out: dict[str, Image.Image] = {}
    for v, im in raw.items():
        im = im.resize((max(1, round(im.width * k * squeeze)), max(1, round(im.height * k))), Image.LANCZOS)
        bb = im.getchannel("A").getbbox()
        cap = open_cap.get(v) if isinstance(open_cap, dict) else open_cap
        if cap and lip and v not in "XA":
            cap /= 1.3  # the lip rim adds ~0.27 of the shape height; the cap applies to the whole mouth
        if cap and bb and bb[3] - bb[1] > cap:  # proportional opening limit, centre kept
            part = im.crop(bb)
            nh = max(1, int(cap))
            canvas = Image.new("RGBA", im.size, (0, 0, 0, 0))
            canvas.paste(part.resize((part.width, nh), Image.LANCZOS), (bb[0], (bb[1] + bb[3] - nh) // 2))
            im = canvas
        out[v] = _lips(im, lip) if lip and v not in "XA" else im
    W = max(i.width for i in out.values()); H = max(i.height for i in out.values())
    for v, im in out.items():  # one shared canvas size again, centred: same anchor, comparable sprites
        c = Image.new("RGBA", (W, H), (0, 0, 0, 0)); c.paste(im, ((W - im.width) // 2, (H - im.height) // 2)); out[v] = c
    return out


def _lips(im: Image.Image, lip: tuple[int, int, int, int]) -> Image.Image:
    """Soft lip-coloured rim under an open shape, so the cavity reads as a mouth in the avatar's own lips
    instead of a flat sticker (thickest below, like a lower lip); edges feathered into the skin."""
    from PIL import ImageFilter  # noqa: PLC0415
    a = im.getchannel("A")
    bb = a.getbbox()
    if not bb:
        return im
    r = max(1, round((bb[3] - bb[1]) * 0.09))
    pad = 3 * r
    big = Image.new("RGBA", (im.width + 2 * pad, im.height + 2 * pad), (0, 0, 0, 0))
    big.paste(im, (pad, pad))
    ab = big.getchannel("A")
    grown = ab.filter(ImageFilter.MaxFilter(2 * r + 1))
    lower = Image.new("L", ab.size, 0)
    lower.paste(grown.crop((0, 0, ab.width, ab.height - r)), (0, r))  # lower lip: one more radius down
    rim = Image.fromarray(np.maximum(np.asarray(grown), np.asarray(lower))).filter(ImageFilter.GaussianBlur(r * 0.6))
    rim = rim.point(lambda q: int(q * 0.78))
    layer = Image.new("RGBA", ab.size, lip[:3] + (0,))
    layer.putalpha(rim)
    layer.alpha_composite(big)
    return layer  # padded on every side: place() centres each sprite on the rest lips, so the anchor holds


def place(lips: tuple[int, int, int, int], sprites: dict[str, Image.Image]) -> dict[str, tuple[Image.Image, tuple[int, int]]]:
    """Every sprite centred on the rest lips' centre: one fixed anchor for the whole set."""
    cx, cy = (lips[0] + lips[2]) / 2, (lips[1] + lips[3]) / 2
    return {v: (im, (round(cx - im.width / 2), round(cy - im.height / 2))) for v, im in sprites.items()}


def describe() -> dict[str, Any]:
    return {"source": "products.aiwake.animator.asset_generator._draw_ghibli_mouth_patch", "visemes": VISEMES}
