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

from . import eyes, rig, styles

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
EXPRESSIONS = [
    ("neutro", "neutral", (0.0, 0.0, 0.0, 0.0)),
    ("alegre", "happy", (-1.5, -1.5, -4.0, 4.0)),
    ("bravo", "angry", (2.5, 2.5, 12.0, -12.0)),
    ("triste", "sad", (-1.0, -1.0, -10.0, 10.0)),
    ("surpreso", "surprised", (-4.0, -4.0, 1.0, -1.0)),
    ("pensativo", "thoughtful", (-1.8, 0.6, -5.0, 4.0)),
    ("irônico", "ironic", (-2.0, 1.0, -8.0, 5.0)),
]
VIEW_LABEL = {"front": "FRENTE", "left": "PERFIL ESQUERDO", "right": "PERFIL DIREITO"}
CELL, PAD, LABEL_H, ROW_HEAD = 190, 14, 30, 34


def _font(weight: str, size: int) -> Any:
    path = _FONT_DIR / f"Montserrat-{weight}.ttf"
    return ImageFont.truetype(str(path), size) if path.is_file() else ImageFont.load_default()


def compose(
    view: dict[str, Any],
    vis: str = "X",
    lids: str = "open",
    gaze: tuple[float, float] = (0.0, 0.0),
    expression: str | None = None,
    brow_pose: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0),
) -> Image.Image:
    """Full character at canvas size (``view`` from :func:`rig.load_view`)."""
    W, H = view["canvas"]["w"], view["canvas"]["h"]
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    layers = {l["name"]: l for l in view["layers"]}
    movable = {"eye_white", "iris", "eye_socket"} <= set(layers)
    mx, my = (view.get("eyeRig") or {}).get("maxShift", (0, 0))
    mouth = (view.get("expressions") or {}).get(expression or "") or view["visemes"].get(vis) or view["visemes"]["X"]
    parts = sorted(view["layers"], key=lambda l: l["z"]) + [mouth]
    for l in sorted(parts, key=lambda l: l["z"]):
        name = l["name"]
        if name == "eye_socket" or (movable and name == "eyes"):
            continue
        if (name == "eyes" and lids == "closed") or (name == "eyes_closed" and lids != "closed") or (name == "eyes_half" and lids != "half"):
            continue
        with Image.open(l["url"]) as im:
            img = im.convert("RGBA")
        xy = (l["x"], l["y"])
        stored = (int(l.get("w") or img.width), int(l.get("h") or img.height))
        if img.size != stored:
            # The viseme file must not bring its own scale. The rig box is shared.
            img = img.resize(stored, Image.Resampling.LANCZOS)
        if name in {"brow_left", "brow_right", "brows"}:
            from . import human_eyes as human_mod  # noqa: PLC0415
            side = 0 if name == "brow_left" else 1
            if name == "brows":
                dy = (brow_pose[0] + brow_pose[1]) / 2
                angle = (brow_pose[2] + brow_pose[3]) / 2
            else:
                dy, angle = brow_pose[side], brow_pose[2 + side]
            info = view.get("humanEyes") or {}
            img, xy = human_mod.pose_brow(img, xy, dy, angle, info.get("browHeight") or max(8, l["h"]), info.get("forehead"))
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
    """Square crop around the face. Image-mode rigs may omit ``boxes``; use the head layer then."""
    own = skin / "rig_front.json"
    path = own if view == "front" and own.exists() else skin / "rig.json"
    face = None
    if path.is_file():
        try:
            face = (json.loads(path.read_text(encoding="utf-8")).get("boxes") or {}).get("face")
        except (OSError, json.JSONDecodeError, AttributeError):
            face = None
    if face and len(face) == 4:
        x0, y0, x1, y1 = (float(v) for v in face)
        w, h = x1 - x0, y1 - y0
        x0, x1 = x0 - w * 0.12, x1 + w * 0.12
        y0, y1 = y0 - h * 0.08, y1 + h * 0.1
        side = max(x1 - x0, y1 - y0)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        if view == "right":
            cx = size[0] - cx
        return (round(cx - side / 2), round(cy - side / 2), round(cx + side / 2), round(cy + side / 2))
    view_path = skin / "views" / view / "view.json"
    if view_path.is_file():
        try:
            layers = json.loads(view_path.read_text(encoding="utf-8")).get("layers") or []
        except (OSError, json.JSONDecodeError):
            layers = []
        head = next((layer for layer in layers if layer.get("name") == "head"), None)
        if head:
            return (int(head["x"]), int(head["y"]), int(head["x"]) + int(head["w"]), int(head["y"]) + int(head["h"]))
    return (0, 0, size[0], size[1])


def build(skin: Path, name: str = "", style: str = "") -> Path:
    """Write ``<skin>/review_sheet.png`` and return its path."""
    views = {v: rig.load_view(skin, v) for v in ("front", "left", "right")}
    cols = max(len(rig.VISEMES), len(EYE_STATES))
    width = PAD + cols * (CELL + PAD)
    block = ROW_HEAD + 3 * (CELL + LABEL_H + PAD)
    height = 90 + len(views) * (block + PAD)
    sheet = Image.new("RGB", (width, height), (16, 17, 20))
    d = ImageDraw.Draw(sheet)
    title, small, tag = _font("Bold", 30), _font("SemiBold", 15), _font("Bold", 16)
    label = styles.STYLES.get(style, {}).get("label", "")
    d.text((PAD, 22), " · ".join(x for x in ("Ficha de revisão", name, f"estilo {label}" if label else "") if x), font=title, fill=(255, 255, 255))
    d.text((PAD, 60), "Bocas South Park/Aiwake, olhos, sobrancelhas e expressões, por vista", font=small, fill=(170, 170, 178))
    y = 90
    for vname, view in views.items():
        size = (view["canvas"]["w"], view["canvas"]["h"])
        box = _face_box(skin, vname, size)
        d.text((PAD, y + 8), VIEW_LABEL[vname] + (" (arte do perfil esquerdo)" if vname == "front" and not (skin / "views" / "front").exists() else ""), font=tag, fill=(245, 176, 66))
        y += ROW_HEAD
        rows = [[(f"{v} · {PHONEMES[v]}", dict(vis=v, gaze=eyes.camera_target(vname, {"left": -1, "right": 1}.get(vname, 0)))) for v in rig.VISEMES],
                [(label, dict(lids=lids, gaze=g)) for label, lids, g in EYE_STATES]
                + [("olha câmera", dict(gaze=eyes.camera_target(vname, {"left": -1, "right": 1}.get(vname, 0))))],
                [(label, dict(expression=expression, brow_pose=pose)) for label, expression, pose in EXPRESSIONS]]
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


def build_views(skin: Path, name: str = "") -> Path:
    """Write a full-character front/left/right layered contact sheet."""
    view_names = ("front", "left", "right")
    views = {view_name: rig.load_view(skin, view_name) for view_name in view_names}
    cell_w, cell_h, header = 430, 620, 72
    out = Image.new("RGB", (cell_w * 3, cell_h + header), (15, 17, 21))
    draw = ImageDraw.Draw(out)
    draw.text(
        (18, 16), f"Three-view layered avatar · {name}".rstrip(" ·"),
        font=_font("Bold", 28), fill=(255, 255, 255),
    )
    for index, view_name in enumerate(view_names):
        image = compose(
            views[view_name],
            vis="X",
            gaze=eyes.camera_target(view_name, {"left": -1, "right": 1}.get(view_name, 0)),
        )
        image.thumbnail((cell_w - 40, cell_h - 54), Image.Resampling.LANCZOS)
        x = index * cell_w + (cell_w - image.width) // 2
        y = header + 40 + (cell_h - 74 - image.height) // 2
        out.paste(image.convert("RGB"), (x, y), image)
        label = {
            "front": "FACING FRONT",
            "left": "FACING LEFT",
            "right": "FACING RIGHT · MIRRORED",
        }[view_name]
        draw.text(
            (index * cell_w + 18, header + 10), label,
            font=_font("SemiBold", 17), fill=(245, 176, 66),
        )
    target = skin / "views_contact_sheet.png"
    out.save(target, "PNG", optimize=True)
    return target
