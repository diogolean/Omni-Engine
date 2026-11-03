"""One mouth anchor per view, shared by every viseme and expression.

The centre sits on the nose-tip → chin span (Einstein rests at 64%, inside
55–75%), under a moustache when there is one, and toward the side the face
points. Front stays on the midline. A left three-quarter view moves left of
the head box; right is that placement mirrored.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

# Nose tip, as a share of the eye-line → chin span. Same line as ``avatar._seat_mouth``.
NOSE_FROM_EYES = 0.22
# Einstein rest lip: just under the moustache, inside the 55–75% band.
ALONG = 0.64
ALONG_LO = 0.55
ALONG_HI = 0.75
# Three-quarter shift, as a share of the head width, toward the gaze.
SIDE = 0.12
SIDE_MIN = 0.04
FRONT_MAX = 0.06
_VIEWS = ("front", "left", "right")


@dataclass(frozen=True)
class Anchor:
    """Canvas point every mouth sprite centres on."""

    x: int
    y: int
    nose: tuple[int, int]
    chin: tuple[int, int]
    view: str
    along: float
    moustache_bottom: int | None = None

    def as_box(self, face: tuple[int, int, int, int] | list[int], width: int) -> tuple[int, int, int, int]:
        """A mouth rectangle centred on this anchor and kept inside the face."""
        fw = max(8, int(face[2] - face[0]))
        w = max(8, min(int(width), int(fw * 0.46)))
        h = max(6, min(w // 3, max(6, int((face[3] - face[1]) * 0.18))))
        x0 = int(round(self.x - w / 2))
        y0 = int(round(self.y - h / 2))
        x1, y1 = x0 + w, y0 + h
        fx0, fy0, fx1, fy1 = (int(v) for v in face)
        if x0 < fx0:
            x1 += fx0 - x0
            x0 = fx0
        if x1 > fx1:
            x0 -= x1 - fx1
            x1 = fx1
        if y0 < fy0:
            y1 += fy0 - y0
            y0 = fy0
        if y1 > fy1:
            y0 -= y1 - fy1
            y1 = fy1
        return (int(x0), int(y0), max(int(x0) + 2, int(x1)), max(int(y0) + 2, int(y1)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "x": self.x,
            "y": self.y,
            "nose": list(self.nose),
            "chin": list(self.chin),
            "view": self.view,
            "along": round(self.along, 3),
            "moustacheBottom": self.moustache_bottom,
        }


def infer_view(
    face: tuple[int, int, int, int] | list[int],
    left_eye: tuple[int, int, int, int] | list[int],
    right_eye: tuple[int, int, int, int] | list[int],
) -> str:
    """Front when the eyes are level and centred. Otherwise the side the features point to.

    A head turned toward screen-left carries both eyes left of the head centre.
    When the midpoint is still central, the wider eye is the near eye and the
    face points that way.
    """
    fw = max(1, int(face[2] - face[0]))
    cx = (int(face[0]) + int(face[2])) / 2
    le = (int(left_eye[0]) + int(left_eye[2])) / 2
    re = (int(right_eye[0]) + int(right_eye[2])) / 2
    off = ((le + re) / 2 - cx) / fw
    lw = max(1, int(left_eye[2] - left_eye[0]))
    rw = max(1, int(right_eye[2] - right_eye[0]))
    ratio = min(lw, rw) / max(lw, rw)
    if abs(off) <= 0.04 and ratio >= 0.80:
        return "front"
    if abs(off) <= 0.04:
        return "left" if lw >= rw else "right"
    return "left" if off < 0 else "right"


def resolve_view(
    view: str,
    face: tuple[int, int, int, int] | list[int],
    left_eye: tuple[int, int, int, int] | list[int],
    right_eye: tuple[int, int, int, int] | list[int],
) -> str:
    """Left and right slots are three-quarter. A front slot is judged from the eyes."""
    name = view if view in _VIEWS else "front"
    if name in ("left", "right"):
        return name
    return infer_view(face, left_eye, right_eye)


def _nose_chin(
    face: tuple[int, int, int, int] | list[int],
    left_eye: tuple[int, int, int, int] | list[int],
    right_eye: tuple[int, int, int, int] | list[int],
    view: str,
) -> tuple[tuple[int, int], tuple[int, int], int]:
    """Nose tip, chin, and the eye-line bottom. Nose x leans with the gaze."""
    fx0, fy0, fx1, fy1 = (int(v) for v in face)
    fw = max(1, fx1 - fx0)
    cx = (fx0 + fx1) // 2
    eye_bottom = max(int(left_eye[3]), int(right_eye[3]))
    eye_bottom = min(max(eye_bottom, fy0 + 1), fy1 - 2)
    chin_y = max(eye_bottom + 8, fy1)
    nose_y = eye_bottom + int(round(NOSE_FROM_EYES * (chin_y - eye_bottom)))
    nose_y = min(chin_y - 4, max(eye_bottom + 2, nose_y))
    sign = {"left": -1, "right": 1}.get(view, 0)
    nose = (int(cx + sign * 0.08 * fw), int(nose_y))
    chin = (int(cx + sign * 0.05 * fw), int(chin_y))
    return nose, chin, eye_bottom


def moustache_bottom(
    image: Image.Image | None,
    face: tuple[int, int, int, int] | list[int],
    nose_y: int,
    chin_y: int,
) -> int | None:
    """Lowest row of a wide moustache between the nose tip and the upper chin.

    Pale facial hair (Einstein) and a dark moustache both count. Skin, the
    eyes, and a collar below the band do not.
    """
    if image is None:
        return None
    span = chin_y - nose_y
    y0 = int(nose_y)
    y1 = int(min(chin_y - 2, nose_y + 0.62 * span))
    if y1 <= y0 + 2:
        return None
    fx0, _fy0, fx1, _fy1 = (int(v) for v in face)
    pad = int((fx1 - fx0) * 0.22)
    x0, x1 = fx0 + pad, fx1 - pad
    if x1 <= x0 + 4:
        return None
    arr = np.asarray(image.convert("RGBA"))
    height, width = arr.shape[:2]
    y0, y1 = max(0, y0), min(height, y1)
    x0, x1 = max(0, x0), min(width, x1)
    if y1 <= y0 or x1 <= x0:
        return None
    crop = arr[y0:y1, x0:x1]
    rgb = crop[:, :, :3].astype(np.int16)
    opaque = crop[:, :, 3] > 128
    lum = rgb.mean(axis=2)
    chroma = rgb.max(axis=2) - rgb.min(axis=2)
    sat = chroma / np.maximum(rgb.max(axis=2), 1)
    pale = opaque & (sat < 0.21) & (lum >= 110) & (lum <= 250)
    dark = opaque & (lum < 80) & (sat < 0.28) & (lum > 12)
    hair = pale | dark
    need = max(4, int(0.22 * hair.shape[1]))
    rows = np.nonzero(hair.sum(axis=1) >= need)[0]
    if rows.size == 0:
        return None
    return y0 + int(rows.max())


def place(
    image: Image.Image | None,
    face: tuple[int, int, int, int] | list[int],
    left_eye: tuple[int, int, int, int] | list[int],
    right_eye: tuple[int, int, int, int] | list[int],
    *,
    view: str,
) -> Anchor:
    """Centre shared by every viseme and expression on this view."""
    facing = resolve_view(view, face, left_eye, right_eye)
    nose, chin, eye_bottom = _nose_chin(face, left_eye, right_eye, facing)
    span = max(1, chin[1] - nose[1])
    hair = moustache_bottom(image, face, nose[1], chin[1])
    gap = max(3, int(0.03 * span))
    y = nose[1] + ALONG * span
    if hair is not None:
        y = max(y, hair + gap)
    y = max(y, eye_bottom + 4)
    if hair is None:
        y = min(nose[1] + ALONG_HI * span, max(nose[1] + ALONG_LO * span, y))
    y = min(chin[1] - gap, max(nose[1] + 2, y))
    fw = max(1, int(face[2] - face[0]))
    cx = (int(face[0]) + int(face[2])) / 2
    sign = {"left": -1, "right": 1}.get(facing, 0)
    x = cx + sign * SIDE * fw
    margin = 0.16 * fw
    x = min(int(face[2]) - margin, max(int(face[0]) + margin, x))
    along = (y - nose[1]) / span
    return Anchor(
        int(round(x)), int(round(y)), nose, chin, facing, float(along), hair,
    )


def seat(
    sprites: dict[str, Image.Image],
    anchor: Anchor,
) -> dict[str, tuple[Image.Image, tuple[int, int]]]:
    """Every sprite's ink centre on ``anchor``. One point, so the puppet does not jump.

    A rotated rest mouth (the sad line) is not centred in its canvas. Centring
    the canvas would leave that ink a few pixels off the shared point.
    """
    placed: dict[str, tuple[Image.Image, tuple[int, int]]] = {}
    for name, sprite in sprites.items():
        ink = _ink_center(sprite, (0, 0))
        ix = sprite.width / 2 if ink is None else ink[0]
        iy = sprite.height / 2 if ink is None else ink[1]
        placed[name] = (sprite, (int(round(anchor.x - ix)), int(round(anchor.y - iy))))
    return placed


def _ink_center(image: Image.Image, origin: tuple[int, int]) -> tuple[float, float] | None:
    box = image.getchannel("A").point(lambda a: 255 if a > 128 else 0).getbbox()
    if not box:
        return None
    return (origin[0] + (box[0] + box[2]) / 2, origin[1] + (box[1] + box[3]) / 2)


def judge(
    center: tuple[float, float],
    image: Image.Image | None,
    face: tuple[int, int, int, int] | list[int],
    left_eye: tuple[int, int, int, int] | list[int],
    right_eye: tuple[int, int, int, int] | list[int],
    *,
    view: str,
) -> dict[str, Any]:
    """Fail when the centre leaves the nose–chin band, sits in the moustache, or faces the wrong way."""
    anchor = place(image, face, left_eye, right_eye, view=view)
    span = max(1, anchor.chin[1] - anchor.nose[1])
    along = (float(center[1]) - anchor.nose[1]) / span
    fw = max(1, int(face[2] - face[0]))
    cx = (int(face[0]) + int(face[2])) / 2
    offset = (float(center[0]) - cx) / fw
    eye_bottom = max(int(left_eye[3]), int(right_eye[3]))
    in_band = anchor.nose[1] < float(center[1]) < anchor.chin[1]
    under_hair = anchor.moustache_bottom is None or float(center[1]) > anchor.moustache_bottom
    below_eyes = float(center[1]) > eye_bottom + 2
    if anchor.view == "left":
        side_ok = offset <= -SIDE_MIN
        side_note = "left view: mouth is not left of the head centre"
    elif anchor.view == "right":
        side_ok = offset >= SIDE_MIN
        side_note = "right view: mouth is not right of the head centre"
    else:
        side_ok = abs(offset) <= FRONT_MAX
        side_note = "front view: mouth is off the midline"
    ok = in_band and under_hair and below_eyes and side_ok
    if ok:
        note = f"{anchor.view}: mouth at {along:.2f} of nose→chin"
    elif not below_eyes:
        note = "mouth is between the eyes"
    elif not in_band:
        note = "mouth is outside the nose-chin band"
    elif not under_hair:
        note = "mouth is inside the moustache"
    else:
        note = side_note
    return {
        "pass": ok,
        "along": round(along, 3),
        "offset": round(offset, 3),
        "view": anchor.view,
        "nose": list(anchor.nose),
        "chin": list(anchor.chin),
        "note": note,
    }


def _open_layer(folder: Path, layer: dict[str, Any]) -> Image.Image | None:
    url = str(layer.get("url") or "")
    path = Path(url) if Path(url).is_file() else folder / url
    if not path.is_file():
        return None
    with Image.open(path) as image:
        return image.convert("RGBA")


def _paste_head(folder: Path, view: dict[str, Any]) -> Image.Image | None:
    layer = next((item for item in view.get("layers") or [] if item.get("name") == "head"), None)
    if layer is None:
        return None
    image = _open_layer(folder, layer)
    if image is None:
        return None
    canvas = Image.new("RGBA", (int(view["canvas"]["w"]), int(view["canvas"]["h"])), (0, 0, 0, 0))
    canvas.alpha_composite(image, (int(layer["x"]), int(layer["y"])))
    return canvas


def _boxes(folder: Path, view: dict[str, Any], skin: Path | None, name: str) -> tuple[list[int], list[int], list[int]] | None:
    qa = view.get("mouthQA") or {}
    eyes = qa.get("eyes") or []
    face = qa.get("face")
    if face and len(eyes) >= 2:
        return list(face), list(eyes[0]), list(eyes[1])
    if skin is None:
        return None
    rig_name = "rig_front.json" if name == "front" and (skin / "rig_front.json").is_file() else "rig.json"
    path = skin / rig_name
    if not path.is_file():
        return None
    boxes = json.loads(path.read_text(encoding="utf-8")).get("boxes") or {}
    if not boxes.get("face") or not boxes.get("left_eye") or not boxes.get("right_eye"):
        return None
    face_box = list(boxes["face"])
    left, right = list(boxes["left_eye"]), list(boxes["right_eye"])
    if name == "right":
        width = int(view["canvas"]["w"])

        def mirror(box: list[int]) -> list[int]:
            return [width - box[2], box[1], width - box[0], box[3]]

        face_box, left, right = mirror(face_box), mirror(right), mirror(left)
    return face_box, left, right


def judge_view(folder: Path, view_name: str, *, skin: Path | None = None) -> dict[str, Any]:
    """Measure the saved X mouth (and that every other mouth shares it)."""
    path = folder / "view.json"
    if not path.is_file():
        return {"pass": False, "along": None, "offset": None, "view": view_name, "note": "no view"}
    view = json.loads(path.read_text(encoding="utf-8"))
    boxes = _boxes(folder, view, skin, view_name)
    if boxes is None:
        return {"pass": False, "along": None, "offset": None, "view": view_name, "note": "no face boxes"}
    face, left_eye, right_eye = boxes
    visemes = view.get("visemes") or {}
    layer = visemes.get("X") or next(iter(visemes.values()), None)
    if layer is None:
        return {"pass": False, "along": None, "offset": None, "view": view_name, "note": "no mouth sprite"}
    sprite = _open_layer(folder, layer)
    if sprite is None:
        return {"pass": False, "along": None, "offset": None, "view": view_name, "note": "mouth file missing"}
    center = _ink_center(sprite, (int(layer["x"]), int(layer["y"])))
    if center is None:
        return {"pass": False, "along": None, "offset": None, "view": view_name, "note": "empty mouth sprite"}
    centres: list[tuple[float, float]] = [center]
    for family in (visemes, view.get("expressions") or {}):
        for extra in family.values():
            image = _open_layer(folder, extra)
            if image is None:
                continue
            found = _ink_center(image, (int(extra["x"]), int(extra["y"])))
            if found is not None:
                centres.append(found)
    # Ink boxes of a rotated rest mouth sit a couple of pixels off the shared point.
    # A mouth seated on the nose is tens of pixels away, so 6px still fails that.
    spread = max(max(abs(c[0] - center[0]), abs(c[1] - center[1])) for c in centres)
    head = _paste_head(folder, view)
    result = judge(center, head, face, left_eye, right_eye, view=view_name)
    if spread > 6:
        result["pass"] = False
        result["note"] = "visemes and expressions do not share one anchor"
    result["spread"] = round(float(spread), 2)
    return result


def _move_layers(view: dict[str, Any], dx: int, dy: int) -> tuple[int, int]:
    """Shift every mouth by one delta. Returns the delta that kept X inside the canvas."""
    canvas = view.get("canvas") or {}
    width, height = int(canvas.get("w") or 0), int(canvas.get("h") or 0)
    visemes = view.get("visemes") or {}
    layer = visemes.get("X") or next(iter(visemes.values()), None)
    if layer is None or width <= 0:
        return (0, 0)
    w, h = int(layer.get("w") or 1), int(layer.get("h") or 1)
    nx = min(max(0, int(layer["x"]) + dx), max(0, width - w))
    ny = min(max(0, int(layer["y"]) + dy), max(0, height - h))
    applied = (nx - int(layer["x"]), ny - int(layer["y"]))
    families = [visemes, view.get("expressions") or {}]
    for family in families:
        for item in family.values():
            item["x"] = int(item["x"]) + applied[0]
            item["y"] = int(item["y"]) + applied[1]
    qa = view.get("mouthQA") or {}
    for key in ("mouth", "mouthBox"):
        box = qa.get(key)
        if box:
            qa[key] = [box[0] + applied[0], box[1] + applied[1], box[2] + applied[0], box[3] + applied[1]]
    return applied


def shift_view(view: dict[str, Any], dx: int, dy: int) -> None:
    """Apply a reposition delta to an in-memory view (the file was already moved)."""
    _move_layers(view, dx, dy)


def reposition_skin(skin: Path) -> dict[str, tuple[int, int]]:
    """Move saved mouths onto the anchor. No image call.

    The returned deltas are what the files moved, so a builder can shift the
    view dict it is about to return by the same amount.
    """
    shifts: dict[str, tuple[int, int]] = {}
    for name in ("left", "front", "right"):
        folder = skin / "views" / name
        path = folder / "view.json"
        if not path.is_file():
            continue
        view = json.loads(path.read_text(encoding="utf-8"))
        boxes = _boxes(folder, view, skin, name)
        visemes = view.get("visemes") or {}
        layer = visemes.get("X") or next(iter(visemes.values()), None)
        if boxes is None or layer is None:
            shifts[name] = (0, 0)
            continue
        sprite = _open_layer(folder, layer)
        center = _ink_center(sprite, (int(layer["x"]), int(layer["y"]))) if sprite is not None else None
        if center is None:
            shifts[name] = (0, 0)
            continue
        face, left_eye, right_eye = boxes
        anchor = place(_paste_head(folder, view), face, left_eye, right_eye, view=name)
        dx, dy = int(round(anchor.x - center[0])), int(round(anchor.y - center[1]))
        applied = _move_layers(view, dx, dy) if dx or dy else (0, 0)
        qa = view.setdefault("mouthQA", {})
        qa["anchor"] = anchor.to_dict()
        qa["face"] = [int(v) for v in face]
        path.write_text(json.dumps(view, indent=2), encoding="utf-8")
        rig_name = {"left": "rig.json", "front": "rig_front.json"}.get(name)
        rig_path = skin / rig_name if rig_name else None
        if rig_path is not None and rig_path.is_file():
            doc = json.loads(rig_path.read_text(encoding="utf-8"))
            box = list(anchor.as_box(face, max(12, int((face[2] - face[0]) * 0.34))))
            doc.setdefault("boxes", {})["mouth"] = box
            doc["mouthBox"] = box
            rig_path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        shifts[name] = applied
    return shifts
