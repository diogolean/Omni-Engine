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
# double_mouth treats sprite alpha below this as "outside" on a human mouth.
_SPRITE_ALPHA = 40


def _contain_ink(image: Image.Image) -> Image.Image:
    """Pull every painted pixel inside the PNG.

    A soft lip fringe (alpha 20–39) blends into mouth ink once it sits on a
    moustache. The double-mouth gate then opens that mask by one pixel, so
    the fringe counts as ink outside the sprite. Grow the opaque core by one
    pixel, copy a neighbour colour onto that ring, drop the softer fringe,
    and leave a clear margin so the stroke is not on the PNG border.
    """
    import cv2  # noqa: PLC0415

    arr = np.array(image.convert("RGBA"))
    alpha = arr[..., 3]
    opaque = (alpha >= _SPRITE_ALPHA).astype(np.uint8)
    if int(opaque.sum()) == 0:
        return image
    kernel = np.ones((3, 3), np.uint8)
    grown = cv2.dilate(opaque, kernel)
    colour = arr[..., :3].copy()
    colour[alpha < _SPRITE_ALPHA] = 0
    filled = cv2.dilate(colour, kernel)
    ring = (grown > 0) & (alpha < _SPRITE_ALPHA)
    arr[ring, :3] = filled[ring]
    arr[..., 3] = np.where(grown > 0, np.maximum(alpha, np.uint8(_SPRITE_ALPHA)), np.uint8(0))
    ys, xs = np.nonzero(arr[..., 3] > 0)
    if xs.size == 0:
        return Image.fromarray(arr, "RGBA")
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    margin = 2
    top, left = max(0, margin - y0), max(0, margin - x0)
    bottom = max(0, margin - (arr.shape[0] - y1))
    right = max(0, margin - (arr.shape[1] - x1))
    if top or left or bottom or right:
        out = np.zeros((arr.shape[0] + top + bottom, arr.shape[1] + left + right, 4), np.uint8)
        out[top:top + arr.shape[0], left:left + arr.shape[1]] = arr
        arr = out
    return Image.fromarray(arr, "RGBA")


def _seal(image: Image.Image) -> Image.Image:
    """Contain the ink, then squash a shape the ring made taller than the opening cap.

    The ring is one pixel. A second contain puts that pixel back without a new fringe.
    """
    image = _contain_ink(image)
    box = image.getchannel("A").getbbox()
    if not box:
        return image
    width, height = box[2] - box[0], box[3] - box[1]
    if width <= 0 or height / width <= OPEN_ASPECT_CAP + 0.02:
        return image
    room = max(1, int(width * OPEN_ASPECT_CAP) - 2)
    return _contain_ink(_limit_opening(image, max_height=room, max_aspect=OPEN_ASPECT_CAP))


def _same_canvas(sprites: dict[str, Image.Image]) -> dict[str, Image.Image]:
    """One size for the set, each sprite centred, so the shared anchor holds."""
    width = max(im.width for im in sprites.values())
    height = max(im.height for im in sprites.values())
    shared: dict[str, Image.Image] = {}
    for name, im in sprites.items():
        if im.size == (width, height):
            shared[name] = im
            continue
        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        canvas.paste(im, ((width - im.width) // 2, (height - im.height) // 2))
        shared[name] = canvas
    return shared


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
            # A squashed front-view cavity becomes a flat black rectangle. Redraw it open.
            if v not in "XA" and _is_black_bar(im):
                width = max(12, int((bb[2] - bb[0]) if bb else lip_width))
                im = _lipped_open(width, {**colours, "lip": lip} if lip else colours)
        out[v] = im
    return _same_canvas({code: _seal(im) for code, im in out.items()})


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
    # Organic wedge, not the closed smug line and not a geometric oval.
    # The face-height cap is applied to the ink after the seal, so a wide
    # smile on a short face cannot reach ~0.18 of the face.
    smile = _open_happy(max(8, int(round(lip_width * squeeze))))
    width, height = max(width, smile.width), max(height, smile.height)
    seated: dict[str, Image.Image] = {}
    for emotion, im in scaled.items():
        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        canvas.paste(im, ((width - im.width) // 2, (height - im.height) // 2))
        seated[emotion] = canvas
    happy = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    happy.paste(smile, ((width - smile.width) // 2, (height - smile.height) // 2))
    seated["happy"] = happy
    sealed = {name: _seal(im) for name, im in seated.items()}
    if open_cap:
        sealed["happy"] = _fit_ink_height(sealed["happy"], float(open_cap))
    return _same_canvas(sealed)


def flat_dark_blob(image: Image.Image) -> bool:
    """One nearly black mass with no teeth or lip contrast: the oval that covers a mustache.

    Visemes E and F are drawn as a single dark ellipse. That reads as a flat sticker
    on a cached Einstein front view. A real mouth has a lighter lip or a teeth crescent.
    """
    arr = np.asarray(image.convert("RGBA"))
    alpha = arr[..., 3]
    ink = alpha > 128
    if int(ink.sum()) < 30:
        return False
    rgb = arr[ink][:, :3].astype(np.float32)
    if float(rgb.mean()) > 60:
        return False
    light = float((rgb.mean(axis=1) > 150).mean())
    return float(rgb.std()) < 28 and light < 0.03


def painted_mouth_is_flat(rgb: np.ndarray) -> bool:
    """Frame crop: the mouth ink is one flat dark blob, not lips plus a cavity."""
    if rgb.ndim != 3 or rgb.shape[2] < 3:
        return False
    lum = rgb.astype(np.float32).mean(axis=2)
    border = np.concatenate([lum[0], lum[-1], lum[:, 0], lum[:, -1]])
    skin = float(np.median(border)) if border.size else 180.0
    ink = lum < skin - 28
    if int(ink.sum()) < 25:
        return False
    cols = rgb[ink][:, :3].astype(np.float32)
    light = float((cols.mean(axis=1) > 150).mean())
    return float(cols.std()) < 18 and light < 0.03 and float(cols.mean()) < 55


def _is_black_bar(image: Image.Image) -> bool:
    """A wide, nearly black fill with square corners: the front-view mouth that reads as a sticker."""
    arr = np.asarray(image)
    if arr.ndim != 3 or arr.shape[2] < 4:
        return False
    alpha = arr[..., 3]
    ys, xs = np.nonzero(alpha > 128)
    if xs.size < 20:
        return False
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())
    width, height = x1 - x0 + 1, y1 - y0 + 1
    if height < 3 or width / height < 2.8:
        return False
    crop = arr[y0:y1 + 1, x0:x1 + 1]
    corners = (int(crop[0, 0, 3]), int(crop[0, -1, 3]), int(crop[-1, 0, 3]), int(crop[-1, -1, 3]))
    rgb = crop[crop[..., 3] > 128][:, :3].astype(np.float32)
    if rgb.size == 0:
        return False
    dark = float((rgb.mean(axis=1) < 42).mean())
    # Square corners, or a wide dark slab (a squashed ellipse still has clear corners).
    return dark > 0.6 and (min(corners) > 180 or width / height >= 3.2)


def _lipped_open(width: int, colours: dict[str, tuple[int, int, int, int]]) -> Image.Image:
    """Open mouth at a readable aspect: ellipse, lip rim, a teeth crescent. Not a flat black bar."""
    from PIL import ImageDraw  # noqa: PLC0415

    cavity = colours.get("cavity", (52, 24, 26, 255))
    rgb = np.array(cavity[:3], np.float32)
    if float(rgb.mean()) < 36:
        cavity = (52, 24, 26, 255)
    outline = colours.get("outline", (40, 24, 26, 255))
    teeth = colours.get("teeth", (246, 242, 236, 255))
    lip = colours.get("lip", outline)
    height = max(8, int(round(width * OPEN_ASPECT_CAP)))
    pad = 6
    image = Image.new("RGBA", (width + 2 * pad, height + 2 * pad), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    box = (pad, pad, pad + width - 2, pad + height - 2)
    draw.ellipse(box, fill=lip)
    inset = max(2, width // 14)
    inner = (box[0] + inset, box[1] + inset, box[2] - inset, box[3] - inset)
    draw.ellipse(inner, fill=cavity)
    teeth_box = (inner[0] + inset, inner[1], inner[2] - inset, inner[1] + max(4, (inner[3] - inner[1]) // 3))
    draw.pieslice(teeth_box, 200, 340, fill=teeth)
    return image


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
    """Open South Park smile: a wide uneven wedge with a teeth band.

    The top is the wide edge and the bottom stays blunt, so it does not read as
    an ellipse. Dark side walls run from the top edge to the bottom so the
    cavity measurement still sees one opening after a 64px resize. Height stays
    under the mouthset aspect cap; the face-height cap is applied later.
    """
    import cv2  # noqa: PLC0415

    width = max(8, int(width))
    # Tall enough that a 3/4 smile still measures open after the 64px cavity
    # resize. Above ~0.48 the contain ring crosses the seal and the squash
    # fills the hole.
    height = max(8, int(round(width * 0.48)))
    stroke = max(2, width // 16)
    pad = stroke + 4
    canvas_w, canvas_h = width + 2 * pad, height + 2 * pad
    scale = 4
    canvas = np.zeros((canvas_h * scale, canvas_w * scale, 4), np.uint8)

    def pt(x: float, y: float) -> list[int]:
        return [int(round(x * scale)), int(round(y * scale))]

    x0, x1 = float(pad + 1), float(pad + width - 1)
    y0, y1 = float(pad + 2), float(pad + height - 2)
    span = max(1.0, x1 - x0)
    # A few percent of lift. Steeper than this and the 15% row is only one corner.
    lift = height * 0.03
    top_l = (x0, y0 + lift * 0.2)
    top_r = (x1, y0 + lift)
    bot_w = span * 0.36
    mid = (x0 + x1) / 2 + span * 0.03
    bot_l = (mid - bot_w / 2, y1)
    bot_r = (mid + bot_w / 2 + span * 0.02, y1 - height * 0.015)
    poly: list[list[int]] = []
    for step in range(9):
        u = step / 8
        poly.append(pt(
            top_l[0] + (top_r[0] - top_l[0]) * u,
            top_l[1] + (top_r[1] - top_l[1]) * u + np.sin(u * np.pi) * height * 0.018,
        ))
    for step in range(1, 6):
        u = step / 5
        inward = np.sin(u * np.pi) * span * 0.015
        poly.append(pt(
            top_r[0] + (bot_r[0] - top_r[0]) * u - inward,
            top_r[1] + (bot_r[1] - top_r[1]) * u,
        ))
    poly.append(pt(*bot_r))
    poly.append(pt(*bot_l))
    for step in range(1, 6):
        u = step / 5
        inward = np.sin(u * np.pi) * span * 0.012
        poly.append(pt(
            bot_l[0] + (top_l[0] - bot_l[0]) * u + inward,
            bot_l[1] + (top_l[1] - bot_l[1]) * u,
        ))
    shape = np.array(poly, np.int32)
    dark = (22, 12, 14, 255)
    outline = (48, 32, 30, 255)
    cv2.fillPoly(canvas, [shape], dark)
    # Teeth sit in the upper middle. The side walls stay dark so the opening
    # is one component from the top lip to the blunt bottom.
    wall = span * 0.18
    band_top = y0 + height * 0.10
    band_bot = y0 + height * 0.42
    teeth: list[list[int]] = []
    for step in range(8):
        u = step / 7
        teeth.append(pt(x0 + wall + (span - 2 * wall) * u, band_top + np.sin(u * np.pi) * height * 0.02))
    for step in range(7, -1, -1):
        u = step / 7
        inset = wall * 0.25
        teeth.append(pt(
            x0 + wall + inset + (span - 2 * wall - 2 * inset) * u,
            band_bot - np.sin(u * np.pi) * height * 0.02,
        ))
    cv2.fillPoly(canvas, [np.array(teeth, np.int32)], (246, 242, 236, 255))
    for frac in (0.36, 0.50, 0.66):
        x = x0 + wall + (span - 2 * wall) * frac
        cv2.line(
            canvas,
            pt(x, band_top + height * 0.04),
            pt(x, band_bot - height * 0.03),
            outline,
            max(1, scale // 2),
        )
    cv2.polylines(canvas, [shape], True, outline, max(1, stroke * scale))
    alpha = canvas[..., 3]
    canvas[..., 3] = np.where(alpha >= _SPRITE_ALPHA, np.maximum(alpha, _SPRITE_ALPHA), 0)
    image = Image.fromarray(canvas, "RGBA").resize((canvas_w, canvas_h), Image.Resampling.NEAREST)
    arr = np.array(image)
    arr[..., 3] = np.where(arr[..., 3] >= _SPRITE_ALPHA, np.maximum(arr[..., 3], _SPRITE_ALPHA), 0)
    return Image.fromarray(arr, "RGBA")


def _ink_box(image: Image.Image) -> tuple[int, int, int, int] | None:
    return image.getchannel("A").point(lambda alpha: 255 if alpha > 128 else 0).getbbox()


def _fit_ink_height(image: Image.Image, limit: float) -> Image.Image:
    """Scale the finished wedge so the opaque ink stays inside ``limit``.

    Drawing the polygon again at a few dozen pixels pinches the dark walls
    and the cavity check reads a closed mouth. Nearest-neighbour from the
    open wedge keeps the same opening and the same top-wide profile.
    """
    box = _ink_box(image)
    if not box:
        return image
    ink_h = box[3] - box[1]
    if ink_h <= limit + 0.5:
        return image
    crop = image.crop(box)
    height = max(8, int(limit))
    fitted = image
    for _ in range(4):
        width = max(8, int(round(crop.width * (height / max(1, crop.height)))))
        small = crop.resize((width, max(8, height)), Image.Resampling.NEAREST)
        arr = np.array(small)
        arr[..., 3] = np.where(arr[..., 3] >= _SPRITE_ALPHA, np.maximum(arr[..., 3], _SPRITE_ALPHA), 0)
        fitted = _contain_ink(Image.fromarray(arr, "RGBA"))
        again = _ink_box(fitted)
        if not again or again[3] - again[1] <= limit + 0.5:
            return fitted
        height = max(8, height - 2)
    return fitted


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


def place(lips: tuple[int, int, int, int], sprites: dict[str, Image.Image], *,
          top: int | None = None) -> dict[str, tuple[Image.Image, tuple[int, int]]]:
    """Every sprite on one anchor.

    ``top`` seats the sprite under the moustache (the blank skin's top edge)
    instead of centring it on a painted lip.
    """
    cx = (lips[0] + lips[2]) / 2
    if top is None:
        cy = (lips[1] + lips[3]) / 2
        return {v: (im, (round(cx - im.width / 2), round(cy - im.height / 2))) for v, im in sprites.items()}
    return {v: (im, (round(cx - im.width / 2), int(top))) for v, im in sprites.items()}


def describe() -> dict[str, Any]:
    return {
        "source": "products.aiwake.animator.asset_generator._draw_ghibli_mouth_patch",
        "visemes": VISEMES,
        "expressions": EMOTIONS,
        "aiwakeRestStates": ["smug_smile", "stressed_grimace"],
    }
