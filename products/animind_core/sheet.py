"""Character review sheet: every mouth viseme and every eye state, per view.

One PNG per character (``<skin>/review_sheet.png``) so a person can check the
rig at a glance: for each view (front, left, right) a row with the 9 Rhubarb
mouths (X, A-H, labelled with their phonemes) and a row with the eye states
(open, half, closed, gaze left/right/up/down and at the camera). Built from
the same layers the renderer uses, no network.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageFont

from . import eyes, rig

_FONT_DIR = Path(__file__).resolve().parents[2] / "Fonts" / "Montserrat" / "static"
PHONEMES = {
    "X": "repouso", "A": "M B P", "B": "K S T EE", "C": "EH AE", "D": "AA (aberto)",
    "E": "AO ER", "F": "UW OW W", "G": "F V", "H": "L",
}
EYE_STATES = [  # (label, lids, gaze)
    ("aberto", "open", (0.0, 0.0)), ("meio", "half", (0.0, 0.0)), ("fechado", "closed", (0.0, 0.0)),
    ("olha ←", "open", (-1.0, 0.0)), ("olha →", "open", (1.0, 0.0)), ("olha ↑", "open", (0.0, -1.0)),
    ("olha ↓", "open", (0.0, 1.0)),
]
VIEW_LABEL = {"front": "FRENTE", "left": "PERFIL ESQUERDO", "right": "PERFIL DIREITO"}
CELL, PAD, LABEL_H, ROW_HEAD = 190, 14, 30, 34


def _font(weight: str, size: int) -> Any:
    path = _FONT_DIR / f"Montserrat-{weight}.ttf"
    return ImageFont.truetype(str(path), size) if path.is_file() else ImageFont.load_default()


def compose(view: dict[str, Any], vis: str = "X", lids: str = "open", gaze: tuple[float, float] = (0.0, 0.0)) -> Image.Image:
    """Full character at canvas size (``view`` from :func:`rig.load_view`)."""
    W, H = view["canvas"]["w"], view["canvas"]["h"]
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    layers = {l["name"]: l for l in view["layers"]}
    movable = {"eye_white", "iris", "eye_socket"} <= set(layers)
    mx, my = (view.get("eyeRig") or {}).get("maxShift", (0, 0))
    parts = sorted(view["layers"], key=lambda l: l["z"]) + [view["visemes"].get(vis) or view["visemes"]["X"]]
    for l in sorted(parts, key=lambda l: l["z"]):
        name = l["name"]
        if name == "eye_socket" or (movable and name == "eyes"):
            continue
        if (name == "eyes" and lids == "closed") or (name == "eyes_closed" and lids != "closed") or (name == "eyes_half" and lids != "half"):
            continue
        with Image.open(l["url"]) as im:
            img = im.convert("RGBA")
        xy = (l["x"], l["y"])
        if name == "iris" and movable:
            s = layers["eye_socket"]
            with Image.open(s["url"]) as im:
                sock = im.convert("RGBA")
            moved = Image.new("RGBA", sock.size, (0, 0, 0, 0))
            moved.paste(img, (xy[0] - s["x"] + round(gaze[0] * mx), xy[1] - s["y"] + round(gaze[1] * my)))
            moved.putalpha(ImageChops.multiply(moved.getchannel("A"), sock.getchannel("A")))
            img, xy = moved, (s["x"], s["y"])
        out.alpha_composite(img, xy)
    return out


def _face_box(skin: Path, view: str, size: tuple[int, int]) -> tuple[int, int, int, int]:
    meta = json.loads((skin / "rig.json").read_text(encoding="utf-8"))
    x0, y0, x1, y1 = meta["boxes"]["face"]
    w, h = x1 - x0, y1 - y0
    x0, x1 = x0 - w * 0.12, x1 + w * 0.12
    y0, y1 = y0 - h * 0.08, y1 + h * 0.1
    side = max(x1 - x0, y1 - y0)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    if view == "right":
        cx = size[0] - cx
    return (round(cx - side / 2), round(cy - side / 2), round(cx + side / 2), round(cy + side / 2))


def build(skin: Path, name: str = "") -> Path:
    """Write ``<skin>/review_sheet.png`` and return its path."""
    views = {v: rig.load_view(skin, v) for v in ("front", "left", "right")}
    cols = max(len(rig.VISEMES), len(EYE_STATES))
    width = PAD + cols * (CELL + PAD)
    block = ROW_HEAD + 2 * (CELL + LABEL_H + PAD)
    height = 90 + len(views) * (block + PAD)
    sheet = Image.new("RGB", (width, height), (16, 17, 20))
    d = ImageDraw.Draw(sheet)
    title, small, tag = _font("Bold", 30), _font("SemiBold", 15), _font("Bold", 16)
    d.text((PAD, 22), f"Ficha de revisão · {name}".rstrip(" ·"), font=title, fill=(255, 255, 255))
    d.text((PAD, 60), "Bocas (visemas Rhubarb X, A–H com fonemas) e estados dos olhos, por vista", font=small, fill=(170, 170, 178))
    y = 90
    for vname, view in views.items():
        size = (view["canvas"]["w"], view["canvas"]["h"])
        box = _face_box(skin, vname, size)
        d.text((PAD, y + 8), VIEW_LABEL[vname] + (" (arte do perfil esquerdo)" if vname == "front" else ""), font=tag, fill=(245, 176, 66))
        y += ROW_HEAD
        rows = [[(f"{v} · {PHONEMES[v]}", dict(vis=v, gaze=eyes.camera_target(vname, {"left": -1, "right": 1}.get(vname, 0)))) for v in rig.VISEMES],
                [(label, dict(lids=lids, gaze=g)) for label, lids, g in EYE_STATES]
                + [("olha câmera", dict(gaze=eyes.camera_target(vname, {"left": -1, "right": 1}.get(vname, 0))))]]
        for row in rows:
            for i, (label, kw) in enumerate(row):
                x = PAD + i * (CELL + PAD)
                img = compose(view, **kw).crop(box)
                cell = Image.new("RGBA", img.size, (44, 46, 54, 255))
                cell.alpha_composite(img)
                sheet.paste(cell.convert("RGB").resize((CELL, CELL), Image.LANCZOS), (x, y))
                d.text((x + CELL / 2, y + CELL + 6), label, font=small, fill=(230, 230, 235), anchor="mt")
            y += CELL + LABEL_H + PAD
        y += PAD
    out = skin / "review_sheet.png"
    sheet.save(out, format="PNG", optimize=True)
    return out
