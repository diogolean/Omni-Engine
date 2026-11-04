"""One mouth anchor per view, shared by every viseme and expression.

The centre sits on the nose-tip → chin span (Einstein rests at 64%, inside
55–75%), under a moustache when there is one, and toward the side the face
points. Front stays on the midline. A left three-quarter view moves left of
the head box; right is that placement mirrored.
"""
from __future__ import annotations

import json
import shutil
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


def _clothing_row(arr: np.ndarray, face: tuple[int, int, int, int] | list[int], y: int) -> bool:
    """A sweater or collar: desaturated cloth across the bust, with no cheek skin left.

    A moustache is desaturated too, but the cheeks beside it stay skin. Cloth
    replaces the whole row.
    """
    height = arr.shape[0]
    if y < 0 or y >= height:
        return False
    fx0, _fy0, fx1, _fy1 = (int(v) for v in face)
    fw = max(1, fx1 - fx0)
    x0, x1 = fx0 + int(fw * 0.08), fx1 - int(fw * 0.08)
    x0, x1 = max(0, x0), min(arr.shape[1], x1)
    if x1 <= x0 + 4:
        return False
    row = arr[y, x0:x1]
    rgb = row[:, :3].astype(np.int16)
    opaque = row[:, 3] > 128
    if float(opaque.mean()) < 0.45:
        return False
    lum = rgb.mean(axis=1)
    sat = (rgb.max(axis=1) - rgb.min(axis=1)) / np.maximum(rgb.max(axis=1), 1)
    skin = opaque & (sat >= 0.18) & (rgb[:, 0] > rgb[:, 2] + 8) & (lum > 70)
    cloth = opaque & (sat < 0.18) & (lum > 60) & (lum < 235)
    return float(cloth.mean()) >= 0.70 and float(skin.mean()) < 0.12


def _on_clothing(
    image: Image.Image | None,
    face: tuple[int, int, int, int] | list[int],
    y: int,
) -> bool:
    if image is None:
        return False
    arr = np.asarray(image.convert("RGBA"))
    hits = sum(
        1 for dy in (0, 3, 6, -3)
        if _clothing_row(arr, face, int(y) + dy)
    )
    return hits >= 2


def _jaw_above_collar(
    image: Image.Image | None,
    face: tuple[int, int, int, int] | list[int],
    eye_bottom: int,
) -> int | None:
    """Chin tip when the face box runs on into a collar. None when the box already ends at the jaw.

    The cheeks are the widest skin. The chin is the narrowest skin below them,
    before a collar makes the outline grow again or the skin stops.
    """
    if image is None:
        return None
    arr = np.asarray(image.convert("RGBA"))
    fx0, fy0, fx1, fy1 = (int(v) for v in face)
    fw = max(1, fx1 - fx0)
    y_start = max(0, int(eye_bottom) + 4)
    y_end = min(arr.shape[0], int(fy1))
    spans: list[float] = []
    for y in range(y_start, y_end):
        row = arr[y, max(0, fx0):min(arr.shape[1], fx1)]
        if row.size == 0:
            spans.append(0.0)
            continue
        rgb = row[:, :3].astype(np.int16)
        alpha = row[:, 3]
        lum = rgb.mean(axis=1)
        sat = (rgb.max(axis=1) - rgb.min(axis=1)) / np.maximum(rgb.max(axis=1), 1)
        skin = (alpha > 128) & (sat >= 0.18) & (rgb[:, 0] > rgb[:, 2] + 8) & (lum > 60)
        xs = np.nonzero(skin)[0]
        if xs.size < max(8, int(0.08 * fw)):
            spans.append(0.0)
        else:
            spans.append(float(xs[-1] - xs[0]) / fw)
    if len(spans) < 8:
        return None
    upper = spans[: max(4, len(spans) // 3)]
    cheek = max(upper) if upper else 0.0
    if cheek < 0.35:
        return None
    min_span = cheek
    chin_i = 0
    for index, span in enumerate(spans):
        if span <= 0.0:
            if min_span < cheek - 0.08 and index > chin_i:
                return y_start + chin_i
            return None
        if span < min_span - 0.015:
            min_span = span
            chin_i = index
        elif span > min_span + 0.10 and index > chin_i + 6 and min_span < cheek - 0.08:
            return y_start + chin_i
    return None


def moustache_bottom(
    image: Image.Image | None,
    face: tuple[int, int, int, int] | list[int],
    nose_y: int,
    chin_y: int,
    *,
    limit: float | None = 0.62,
) -> int | None:
    """Lowest row of a wide moustache between the nose tip and the chin.

    Pale facial hair (Einstein) and a dark moustache both count. Skin, the
    eyes, and a collar or sweater below the jaw do not. A second pale band
    under a gap (sideburn, shirt) is not the moustache. ``limit`` caps the
    search as a share of the nose–chin span; a jaw that already stops above
    the collar searches the whole span.
    """
    if image is None:
        return None
    y0 = int(nose_y)
    span = int(chin_y) - y0
    y1 = int(chin_y) - 2 if limit is None else int(min(chin_y - 2, y0 + limit * span))
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
    kept = [int(row) for row in rows if not _clothing_row(arr, face, y0 + int(row))]
    if not kept:
        return None
    band = [kept[0]]
    for row in kept[1:]:
        if row - band[-1] > 4:
            if len(band) >= 3:
                break
            band = [row]
            continue
        band.append(row)
    if len(band) < 3 and len(kept) >= 3:
        band = kept[:1]
        for row in kept[1:]:
            if row - band[-1] > 4:
                break
            band.append(row)
    return y0 + int(band[-1])


def place(
    image: Image.Image | None,
    face: tuple[int, int, int, int] | list[int],
    left_eye: tuple[int, int, int, int] | list[int],
    right_eye: tuple[int, int, int, int] | list[int],
    *,
    view: str,
) -> Anchor:
    """Centre shared by every viseme and expression on this view.

    A face box that includes the sweater used to call the collar the chin, so
    64% of that span sat on the ribbing. The jaw above the cloth is the chin,
    and the lip is the skin just under the moustache.
    """
    facing = resolve_view(view, face, left_eye, right_eye)
    nose, chin, eye_bottom = _nose_chin(face, left_eye, right_eye, facing)
    jaw = _jaw_above_collar(image, face, eye_bottom)
    face_h = max(1, int(face[3]) - int(face[1]))
    long_jaw = jaw is not None and jaw < chin[1] - max(16, int(0.06 * face_h))
    if long_jaw and jaw is not None:
        chin_y = int(jaw)
        span_eye = max(8, chin_y - eye_bottom)
        nose_y = eye_bottom + int(round(NOSE_FROM_EYES * span_eye))
        nose_y = min(chin_y - 4, max(eye_bottom + 2, nose_y))
        nose = (nose[0], int(nose_y))
        chin = (chin[0], chin_y)
    span = max(1, chin[1] - nose[1])
    hair = moustache_bottom(image, face, nose[1], chin[1], limit=None if long_jaw else 0.62)
    # A tight head keeps the small gap. A jaw above a collar needs the lip
    # clear of the moustache, not sitting in the last white hairs.
    gap = max(3, int(0.03 * span))
    if long_jaw:
        gap = max(gap, 12, int(0.08 * span))
    y = nose[1] + ALONG * span
    if hair is not None:
        lip = hair + gap
        # 64% of a long jaw, or a point that has landed on the sweater, is not the lip.
        if _on_clothing(image, face, int(y)) or (long_jaw and y - lip > max(8, int(0.06 * span))):
            floor = lip if _on_clothing(image, face, int(y)) and not long_jaw else nose[1] + ALONG_LO * span
            y = max(lip, floor)
        else:
            y = max(y, lip)
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
        if name == "front" and anchor.view != "front":
            # The folder is named front, but the plate is three-quarter.
            # Drop it instead of recording anchor.view "left" under views/front.
            shutil.rmtree(folder)
            rig_front = skin / "rig_front.json"
            if rig_front.is_file():
                rig_front.unlink()
            continue
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


def _ink_bbox(image: Image.Image, *, threshold: int = 8) -> tuple[int, int, int, int] | None:
    box = image.getchannel("A").point(lambda alpha: 255 if alpha > threshold else 0).getbbox()
    return box


def _scale_ink(image: Image.Image, factor: float) -> Image.Image:
    """Grow the painted mouth, not the empty canvas around it."""
    if factor <= 1.04:
        return image
    box = _ink_bbox(image, threshold=0)
    if not box:
        return image
    part = image.crop(box)
    size = (max(1, int(round(part.width * factor))), max(1, int(round(part.height * factor))))
    grown = part.resize(size, Image.Resampling.LANCZOS)
    margin = 4
    canvas = Image.new("RGBA", (grown.width + 2 * margin, grown.height + 2 * margin), (0, 0, 0, 0))
    canvas.paste(grown, (margin, margin), grown)
    return canvas


def _fit_mouth(image: Image.Image, face: list[int] | tuple[int, int, int, int]) -> Image.Image:
    """Grow a speck of a mouth up to the lip. One viseme that is already wide stays put.

    The soft shadow is dropped before the scale. Grown and then dilated, it
    reads as a grey halo around the opening. Height stays under the mouthOpen
    floor (6% of the face box) so a thin rest lip can sit next to a taller vowel.
    """
    arr = np.array(image.convert("RGBA"))
    arr[..., 3] = np.where(arr[..., 3] >= 128, arr[..., 3], 0).astype(np.uint8)
    image = Image.fromarray(arr, "RGBA")
    box = _ink_bbox(image, threshold=128)
    if not box:
        return image
    width, height = box[2] - box[0], box[3] - box[1]
    face_w = max(1, int(face[2]) - int(face[0]))
    face_h = max(1, int(face[3]) - int(face[1]))
    target_w = face_w * 0.16
    max_h = face_h * 0.058
    factor = 1.0
    if width < target_w * 0.72:
        factor = target_w / max(1, width)
    if height * factor > max_h:
        factor = max_h / max(1, height)
    factor = min(5.0, max(1.0, factor))
    fitted = _scale_ink(image, factor)
    out = np.array(fitted)
    out[..., 3] = np.where(out[..., 3] >= 160, np.uint8(255), np.uint8(0))
    # One opaque pixel of lip colour covers the gate's 2×2 open, which
    # otherwise counts the skin just beside a narrow viseme as ink outside it.
    from . import mouthset  # noqa: PLC0415

    return mouthset._contain_ink(Image.fromarray(out, "RGBA"))


def _clear_painted_lip(
    folder: Path,
    view: dict[str, Any],
    face: tuple[int, int, int, int] | list[int],
    anchor: Anchor,
) -> None:
    """Paint the photographed smile back to cheek skin so the viseme is the only mouth.

    The moustache stays. The collar and the chin below the lip stay.
    """
    import cv2  # noqa: PLC0415

    layer = next((item for item in view.get("layers") or [] if item.get("name") == "head"), None)
    if layer is None:
        return
    path = folder / str(layer.get("url") or "")
    if not path.is_file():
        return
    image = Image.open(path).convert("RGBA")
    arr = np.array(image)
    ox, oy = int(layer["x"]), int(layer["y"])
    fw = max(1, int(face[2]) - int(face[0]))
    hair = anchor.moustache_bottom if anchor.moustache_bottom is not None else anchor.y - 8
    y0 = int(hair) - 2 - oy
    y1 = int(anchor.y + max(16, int(0.07 * max(1, anchor.chin[1] - anchor.nose[1])))) - oy
    half = int(fw * 0.18)
    x0 = int(anchor.x) - half - ox
    x1 = int(anchor.x) + half - ox
    height, width = arr.shape[:2]
    y0, x0 = max(0, y0), max(0, x0)
    y1, x1 = min(height, y1), min(width, x1)
    if y1 <= y0 + 2 or x1 <= x0 + 2:
        return
    rgb = arr[:, :, :3].astype(np.int16)
    alpha = arr[:, :, 3]
    lum = rgb.mean(axis=2)
    sat = (rgb.max(axis=2) - rgb.min(axis=2)) / np.maximum(rgb.max(axis=2), 1)
    opaque = alpha > 128
    skin = opaque & (sat >= 0.18) & (rgb[:, :, 0] > rgb[:, :, 2] + 8) & (lum > 80)
    sample_box = (
        max(0, y0 - 8),
        min(height, y1 + 10),
        max(0, x0 - int(fw * 0.06)),
        min(width, x1 + int(fw * 0.06)),
    )
    ring = skin[sample_box[0]:sample_box[1], sample_box[2]:sample_box[3]].copy()
    ring[y0 - sample_box[0]:y1 - sample_box[0], x0 - sample_box[2]:x1 - sample_box[2]] = False
    if int(ring.sum()) < 12:
        return
    cheek = np.median(rgb[sample_box[0]:sample_box[1], sample_box[2]:sample_box[3]][ring], axis=0)
    cheek_lum = float(cheek.mean())
    sub_opaque = opaque[y0:y1, x0:x1]
    sub_lum = lum[y0:y1, x0:x1]
    sub_sat = sat[y0:y1, x0:x1]
    hair_px = sub_opaque & (sub_sat < 0.20) & (sub_lum >= 115) & (sub_lum <= 250)
    hair_px |= sub_opaque & (sub_lum < 75) & (sub_sat < 0.28)
    crease = sub_opaque & ~hair_px & (sub_lum < cheek_lum - 16)
    if int(crease.sum()) < 8:
        return
    soft = cv2.GaussianBlur(crease.astype(np.float32), (0, 0), 1.1)
    colour = arr[y0:y1, x0:x1, :3].astype(np.float32)
    colour = colour * (1.0 - soft[..., None]) + cheek.astype(np.float32) * soft[..., None]
    arr[y0:y1, x0:x1, :3] = np.clip(colour, 0, 255).astype(np.uint8)
    Image.fromarray(arr, "RGBA").save(path)


def _family_sprites(
    folder: Path,
    view: dict[str, Any],
    family: str,
    face: list[int] | tuple[int, int, int, int],
) -> dict[str, Image.Image]:
    from . import mouthset  # noqa: PLC0415

    scaled: dict[str, Image.Image] = {}
    for key, layer in (view.get(family) or {}).items():
        path = folder / layer["url"]
        if not path.is_file():
            continue
        scaled[key] = _fit_mouth(Image.open(path).convert("RGBA"), face)
    return mouthset._same_canvas(scaled) if scaled else {}


def _write_mouth_box(skin: Path, name: str, box: list[int]) -> None:
    rig_name = {"left": "rig.json", "front": "rig_front.json"}.get(name)
    if rig_name is None:
        return
    path = skin / rig_name
    if not path.is_file():
        return
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc.setdefault("boxes", {})["mouth"] = box
    doc["mouthBox"] = box
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def repair_collar_mouths(skin: Path) -> dict[str, Any]:
    """Seat a photographed plate's mouths on the lip when they were saved on the collar.

    A tight face, or a mouth already on the lip, is left alone. No image call.
    The front folder stays even when the plate is three-quarter: dropping it
    would hide the only photographed bust.
    """
    notes: dict[str, Any] = {}
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
            notes[name] = {"moved": False, "reason": "no mouth"}
            continue
        sprite = _open_layer(folder, layer)
        center = _ink_center(sprite, (int(layer["x"]), int(layer["y"]))) if sprite is not None else None
        if center is None:
            notes[name] = {"moved": False, "reason": "empty"}
            continue
        face, left_eye, right_eye = boxes
        head = _paste_head(folder, view)
        anchor = place(head, face, left_eye, right_eye, view=name)
        on_cloth = _on_clothing(head, face, int(center[1]))
        if abs(center[1] - anchor.y) <= 28 and not on_cloth:
            notes[name] = {"moved": False, "y": int(round(center[1]))}
            continue
        _clear_painted_lip(folder, view, face, anchor)
        seated_sprites: dict[str, Image.Image] = {}
        seated_layers: dict[str, dict[str, Any]] = {}
        for family in ("visemes", "expressions"):
            shared = _family_sprites(folder, view, family, face)
            for key, image in shared.items():
                token = f"{family}:{key}"
                seated_sprites[token] = image
                seated_layers[token] = view[family][key]
        placed = seat(seated_sprites, anchor)
        for token, (image, xy) in placed.items():
            item = seated_layers[token]
            image.save(folder / item["url"], "PNG")
            item["x"], item["y"] = int(xy[0]), int(xy[1])
            item["w"], item["h"] = image.size
        rest = placed.get("visemes:X")
        if rest is None:
            notes[name] = {"moved": False, "reason": "no rest mouth"}
            continue
        rest_image, rest_xy = rest
        ink = _ink_bbox(rest_image, threshold=128)
        if ink is None:
            mouth_box = list(anchor.as_box(face, max(12, int((int(face[2]) - int(face[0])) * 0.22))))
        else:
            mouth_box = [
                int(rest_xy[0] + ink[0]), int(rest_xy[1] + ink[1]),
                int(rest_xy[0] + ink[2]), int(rest_xy[1] + ink[3]),
            ]
        meta = view.setdefault("mouthQA", {})
        meta["anchor"] = anchor.to_dict()
        meta["face"] = [int(v) for v in face]
        meta["mouth"] = mouth_box
        meta["mouthBox"] = mouth_box
        # The photographed smile stays on the head. It is not a mouthless cut,
        # and the collar used to hide that from the blank-skin check.
        meta["mouthless"] = False
        path.write_text(json.dumps(view, indent=2), encoding="utf-8")
        _write_mouth_box(skin, name, mouth_box)
        notes[name] = {
            "moved": True,
            "from": [int(round(center[0])), int(round(center[1]))],
            "to": [anchor.x, anchor.y],
            "onCloth": on_cloth,
        }
    return notes
