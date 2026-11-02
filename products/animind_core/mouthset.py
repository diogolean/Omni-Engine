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
EMOTIONS = ("neutral", "happy", "angry", "sad", "surprised", "thoughtful", "ironic")
_REF_PLATE = (400, 300)  # library units: patch 264 x 150, widest shape ~0.73 x patch width
OPEN_ASPECT_CAP = 0.48  # drawn height / width; speech never becomes a scream


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
    raw = {
        v: _draw_ghibli_mouth_patch(
            v, plate_width=_REF_PLATE[0], plate_height=_REF_PLATE[1], organic=True, **colours
        ).rotate(-1.5 if v in "XABCGH" else 1.0, Image.Resampling.BICUBIC, expand=False)
        for v in VISEMES
    }
    # E is a rounded vowel. Aiwake's patch collapses to a closed line once the
    # left view squeezes it. Draw an open round hole, and a smaller one for F.
    raw["E"] = _round_open(max(28, int(round(_REF_PLATE[0] * 0.34))))
    raw["F"] = _round_open(max(18, int(round(_REF_PLATE[0] * 0.22))))
    pw = raw["X"].width
    k = lip_width / (0.73 * pw)
    out: dict[str, Image.Image] = {}
    for v, im in raw.items():
        im = im.resize((max(1, round(im.width * k * squeeze)), max(1, round(im.height * k))), Image.LANCZOS)
        bb = im.getchannel("A").getbbox()
        cap = open_cap.get(v) if isinstance(open_cap, dict) else open_cap
        if cap and lip and v not in "XA":
            cap /= 1.3  # the lip rim adds ~0.27 of the shape height; the cap applies to the whole mouth
        if v in "EF" and cap and bb and bb[3] - bb[1] > cap:
            # Squashing a round hole into a wide slot closes it. Redraw the hole
            # at the allowed height so E stays the open viseme frame QA measures.
            im = _round_open(max(12, int(float(cap) / OPEN_ASPECT_CAP)))
        elif cap and bb and bb[3] - bb[1] > cap:  # proportional opening limit, centre kept
            part = im.crop(bb)
            nh = max(1, int(cap))
            canvas = Image.new("RGBA", im.size, (0, 0, 0, 0))
            canvas.paste(part.resize((part.width, nh), Image.LANCZOS), (bb[0], (bb[1] + bb[3] - nh) // 2))
            im = canvas
        if v == "E":
            # Keep a round open hole. A lip rim or a height squash closes it,
            # and the left view then fails frame QA around cavity 0.36.
            allowed = int(cap) if cap else int(round(lip_width * squeeze * 0.5))
            im = _round_open(max(12, int(allowed / OPEN_ASPECT_CAP)))
        else:
            im = _lips(im, lip) if lip and v not in "XA" else im
            im = _limit_opening(im, max_aspect=OPEN_ASPECT_CAP)
        out[v] = im
    W = max(i.width for i in out.values())
    H = max(i.height for i in out.values())
    for v, im in out.items():  # one shared canvas size again, centred: same anchor, comparable sprites
        c = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        c.paste(im, ((W - im.width) // 2, (H - im.height) // 2))
        out[v] = c
    return out


def draw_expressions(
    lip_width: float,
    colours: dict[str, tuple[int, int, int, int]],
    *,
    squeeze: float = 1.0,
    open_cap: float | None = None,
) -> dict[str, Image.Image]:
    """Aiwake rest mouths adapted to the seven Animind emotions.

    Happy and ironic deliberately retain Aiwake's asymmetric triangular
    ``smug_smile``; angry uses its ``stressed_grimace``. The remaining
    variants are restrained transforms of the same organic X/E/D artwork.
    """
    from products.aiwake.animator.asset_generator import _draw_ghibli_mouth_patch  # noqa: PLC0415

    colours = {k: v for k, v in colours.items() if k != "lip"}
    source = {
        "neutral": ("X", "neutral", 0.0, 1.0, 1.0),
        # Tall enough that the left-view rest measures about 0.38 open. A
        # squashed smug reads as a closed line and fails frame QA.
        "happy": ("X", "smug_smile", -2.0, 1.0, 1.15),
        "angry": ("X", "stressed_grimace", 1.5, 0.9, 0.7),
        "sad": ("X", "neutral", 7.0, 0.8, 1.0),
        "surprised": ("E", "neutral", -1.0, 0.62, 0.78),
        "thoughtful": ("X", "neutral", -4.0, 0.72, 1.0),
        "ironic": ("X", "smug_smile", -5.0, 0.86, 0.62),
    }
    raw: dict[str, Image.Image] = {}
    for emotion, (viseme, rest, angle, sx, sy) in source.items():
        im = _draw_ghibli_mouth_patch(
            viseme, plate_width=_REF_PLATE[0], plate_height=_REF_PLATE[1],
            organic=True, rest_state=rest, **colours,
        )
        bb = im.getchannel("A").getbbox()
        if bb:
            part = im.crop(bb)
            part = part.resize((max(1, round(part.width * sx)), max(1, round(part.height * sy))), Image.LANCZOS)
            canvas = Image.new("RGBA", im.size, (0, 0, 0, 0))
            canvas.paste(part, ((im.width - part.width) // 2, (im.height - part.height) // 2))
            im = canvas.rotate(angle, Image.Resampling.BICUBIC, expand=False)
        raw[emotion] = im
    k = lip_width / (0.73 * raw["neutral"].width)
    scaled = {
        emotion: _limit_opening(im.resize(
            (max(1, round(im.width * k * squeeze)), max(1, round(im.height * k))),
            Image.LANCZOS,
        ), max_height=open_cap, max_aspect=OPEN_ASPECT_CAP)
        for emotion, im in raw.items()
    }
    width, height = max(i.width for i in scaled.values()), max(i.height for i in scaled.values())
    for emotion, im in scaled.items():
        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        canvas.paste(im, ((width - im.width) // 2, (height - im.height) // 2))
        scaled[emotion] = canvas
    # The squashed smug smile measures closed (~0.20). Frame QA on the left
    # view expects the happy rest to be an open smile, about 0.38.
    smile = _open_happy(max(8, int(round(lip_width * squeeze))))
    seated = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    seated.paste(smile, ((width - smile.width) // 2, (height - smile.height) // 2))
    scaled["happy"] = seated
    return scaled


def _round_open(width: int) -> Image.Image:
    """Open round viseme. Height stays inside the aspect cap so the hole is not squashed shut."""
    from PIL import ImageDraw  # noqa: PLC0415

    height = max(8, int(round(width * OPEN_ASPECT_CAP)))
    image = Image.new("RGBA", (width + 16, height + 16), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse(
        (8, 8, 8 + width - 2, 8 + height - 2),
        fill=(18, 10, 12, 255),
        outline=(40, 24, 26, 255),
        width=max(2, width // 40),
    )
    return image


def _open_happy(width: int) -> Image.Image:
    """Open South Park smile. Aspect stays at the mouthset cap so the cavity can reach ~0.34-0.38."""
    from PIL import ImageDraw  # noqa: PLC0415

    height = max(8, int(round(width * OPEN_ASPECT_CAP)))
    image = Image.new("RGBA", (width + 8, height + 8), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse(
        (4, 4, 4 + width - 2, 4 + height - 2),
        fill=(18, 10, 12, 255),
        outline=(40, 24, 26, 255),
        width=max(2, width // 48),
    )
    return image


def _limit_opening(
    image: Image.Image,
    *,
    max_height: float | None = None,
    max_aspect: float = OPEN_ASPECT_CAP,
) -> Image.Image:
    """Flatten a mouth around its fixed centre when its final painted shape is too tall.

    The South-Park-style O and pucker sprites are naturally narrow.  A face-height
    cap alone therefore allowed a cavity taller than its width in close-up.  This
    limit is applied after lips/rims are drawn, so every viseme and emotional rest
    mouth obeys the same visible opening contract.
    """
    box = image.getchannel("A").getbbox()
    if not box:
        return image
    width, height = box[2] - box[0], box[3] - box[1]
    limit = min(
        height,
        max(1, int(width * max_aspect)),
        max(1, int(max_height)) if max_height is not None else height,
    )
    if height <= limit:
        return image
    part = image.crop(box).resize((width, limit), Image.LANCZOS)
    out = Image.new("RGBA", image.size, (0, 0, 0, 0))
    out.paste(part, (box[0], round((box[1] + box[3] - limit) / 2)))
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
    return {
        "source": "products.aiwake.animator.asset_generator._draw_ghibli_mouth_patch",
        "visemes": VISEMES,
        "expressions": EMOTIONS,
        "aiwakeRestStates": ["smug_smile", "stressed_grimace"],
    }
