"""Midline front from a three-quarter plate. No image call.

The front gate reads detector boxes, not a vibe check. A photographed bust
fails when the eyes and the mouth sit off the face-box centre (Einstein's
cut is about 0.12 of the face width). This mirrors the near half across the
feature midline and slides that midline onto the face centre, then seats the
mouths on the lip again. Left and right stay the three-quarter plate.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

_BOXES = ("face", "left_eye", "right_eye", "mouth")


def _load(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _centre(box: list[int] | tuple[int, ...]) -> float:
    return (int(box[0]) + int(box[2])) / 2


def _axis(boxes: dict[str, list[int]]) -> float:
    """Feature midline: halfway between the eye midpoint and the mouth."""
    eyes = (_centre(boxes["left_eye"]) + _centre(boxes["right_eye"])) / 2
    return (eyes + _centre(boxes["mouth"])) / 2


def _symmetry(boxes: dict[str, Any] | None) -> dict[str, Any]:
    from . import qa  # noqa: PLC0415

    if not boxes or any(key not in boxes for key in _BOXES):
        return {"pass": False, "value": None, "limit": qa.LIMITS["frontOffset"]}
    return qa.front_symmetry({key: list(boxes[key]) for key in _BOXES})


def symmetrize_sprite(
    image: Image.Image,
    origin_x: int,
    axis: int,
    *,
    keep_left: bool,
) -> tuple[Image.Image, int]:
    """Reflect the far side of one sprite across ``axis``. The near side stays.

    The returned sprite covers only the symmetric span, so a wide far-side
    fringe (hair on a three-quarter cut) does not stay glued to one cheek.
    """
    arr = np.asarray(image.convert("RGBA"))
    height, width = arr.shape[:2]
    if width < 2 or height < 2:
        return image, origin_x
    opaque = np.flatnonzero(arr[:, :, 3].max(axis=0) > 8)
    if opaque.size == 0:
        return image, origin_x
    canvas_x = origin_x + opaque
    if keep_left:
        near = canvas_x[canvas_x <= axis]
        if near.size == 0:
            return image, origin_x
        radius = int(axis - int(near.min()))
    else:
        near = canvas_x[canvas_x >= axis]
        if near.size == 0:
            return image, origin_x
        radius = int(int(near.max()) - axis)
    radius = max(1, radius)
    left = int(axis) - radius
    right = int(axis) + radius + 1
    new_w = right - left
    canvas = np.zeros((height, new_w, 4), np.uint8)
    src_x0 = max(int(origin_x), left)
    src_x1 = min(int(origin_x) + width, right)
    if src_x1 > src_x0:
        canvas[:, src_x0 - left:src_x1 - left] = arr[:, src_x0 - origin_x:src_x1 - origin_x]
    cols = np.arange(left, right)
    far = cols > axis if keep_left else cols < axis
    source = (2 * axis - cols).astype(int) - left
    local = np.flatnonzero(far)
    for index in local:
        src = int(source[index])
        if 0 <= src < new_w:
            canvas[:, index] = canvas[:, src]
        else:
            canvas[:, index] = 0
    seam = int(axis) - left
    if 0 < seam < new_w - 1:
        # One column of blend so the join is not a razor cut through the nose.
        blended = (
            canvas[:, seam].astype(np.float32) * 0.65
            + canvas[:, seam + (1 if keep_left else -1)].astype(np.float32) * 0.35
        )
        canvas[:, seam] = blended.astype(np.uint8)
    # The crop ends on the first opaque column. A 1px clear margin keeps the
    # eyelid and hair sprites inside the alpha-edge gate (border must be 0).
    pad = 1
    padded = np.zeros((height + 2 * pad, new_w + 2 * pad, 4), np.uint8)
    padded[pad:pad + height, pad:pad + new_w] = canvas
    return Image.fromarray(padded), left - pad


def _fit_shift(
    placed: list[tuple[dict[str, Any], Image.Image, int]],
    canvas_w: int,
    desired: int,
) -> int:
    """Move the midline toward the face centre without leaving the frame."""
    if not placed or canvas_w < 2:
        return 0

    def fits(delta: int) -> bool:
        for _layer, image, origin in placed:
            x0 = int(origin) + delta
            if x0 < 0 or x0 + image.width > canvas_w:
                return False
        return True

    if fits(desired):
        return desired
    step = 1 if desired > 0 else -1
    best = 0
    for delta in range(0, desired + step, step):
        if not fits(delta):
            break
        best = delta
    return best


def _box_at(centre: float, box: list[int], width: int) -> list[int]:
    x0 = int(round(centre - width / 2))
    return [x0, int(box[1]), x0 + int(width), int(box[3])]


def _face_at(face: list[int], centre: float) -> list[int]:
    width = int(face[2]) - int(face[0])
    return _box_at(centre, face, width)


def _map_brow(model: dict[str, Any], axis: int, shift: int, *, keep_left: bool) -> dict[str, Any]:
    """Near brow stays. The far brow becomes that stroke reflected."""
    strokes = model.get("strokes") or {}
    near_name = "left" if keep_left else "right"
    far_name = "right" if keep_left else "left"
    near = strokes.get(near_name)
    if not isinstance(near, dict) or not near.get("points"):
        return model
    points = [(float(point[0]), float(point[1])) for point in near["points"]]
    thickness = [float(item) for item in near.get("thickness") or []]
    colour = near.get("color")

    def moved(x: float, reflected: bool) -> float:
        if reflected:
            x = (2 * axis) - x
        return round(x + shift, 2)

    near_points = [[moved(x, False), round(y, 2)] for x, y in points]
    far_points = [[moved(x, True), round(y, 2)] for x, y in reversed(points)]
    far_thickness = list(reversed(thickness)) if thickness else thickness
    out = {
        "style": model.get("style") or "illustrated",
        "strokes": {
            near_name: {"points": near_points, "thickness": thickness, "color": colour},
            far_name: {"points": far_points, "thickness": far_thickness, "color": colour},
        },
    }
    return out


def _span(x0: float, x1: float, axis: int, shift: int, *, keep_left: bool) -> list[float]:
    if keep_left:
        left = min(float(x0), float(x1), float(axis))
        reach = float(axis) - left
        a, b = float(axis) - reach, float(axis) + reach
    else:
        right = max(float(x0), float(x1), float(axis))
        reach = right - float(axis)
        a, b = float(axis) - reach, float(axis) + reach
    return [round(a + shift, 2), round(b + shift, 2)]


def _map_human(doc: dict[str, Any], axis: int, shift: int, *, keep_left: bool) -> None:
    iris = doc.get("iris")
    if isinstance(iris, list) and len(iris) >= 2:
        x = float(iris[0])
        if (keep_left and x > axis) or (not keep_left and x < axis):
            x = (2 * axis) - x
        iris[0] = round(x + shift, 2)
    for key in ("sclera", "forehead"):
        box = doc.get(key)
        if isinstance(box, list) and len(box) >= 4:
            a, b = _span(box[0], box[2], axis, shift, keep_left=keep_left)
            box[0], box[2] = a, b


def _centered_features(
    boxes: dict[str, list[int]],
    axis: int,
    shift: int,
    *,
    keep_left: bool,
) -> dict[str, list[int]]:
    """Eyes and mouth centred on the landed midline. Vertical edges stay put."""
    landed = float(axis + shift)
    near = boxes["left_eye"] if keep_left else boxes["right_eye"]
    near_x = _centre(near)
    if keep_left and near_x > axis:
        near = boxes["right_eye"]
        near_x = _centre(near)
    elif not keep_left and near_x < axis:
        near = boxes["left_eye"]
        near_x = _centre(near)
    dist = abs(float(axis) - near_x)
    width = max(2, int(near[2]) - int(near[0]))
    mouth = boxes["mouth"]
    mouth_w = max(2, int(mouth[2]) - int(mouth[0]))
    return {
        "face": _face_at(boxes["face"], landed),
        "left_eye": _box_at(landed - dist, boxes["left_eye"], width),
        "right_eye": _box_at(landed + dist, boxes["right_eye"], width),
        "mouth": _box_at(landed, mouth, mouth_w),
    }


def _reseat_mouths(
    folder: Path,
    view: dict[str, Any],
    face: list[int],
    left_eye: list[int],
    right_eye: list[int],
) -> None:
    """One frontal anchor for every viseme and expression. Sprites are not redrawn."""
    from . import mouth_anchor  # noqa: PLC0415

    qa = view.setdefault("mouthQA", {})
    qa["face"] = [int(v) for v in face]
    qa["eyes"] = [[int(v) for v in left_eye], [int(v) for v in right_eye]]
    head = mouth_anchor._paste_head(folder, view)
    anchor = mouth_anchor.place(head, face, left_eye, right_eye, view="front")
    for family in ("visemes", "expressions"):
        group = view.get(family) or {}
        sprites: dict[str, Image.Image] = {}
        for key, layer in group.items():
            path = folder / str(layer.get("url") or "")
            if path.is_file():
                sprites[key] = Image.open(path).convert("RGBA")
        if not sprites:
            continue
        for key, (image, xy) in mouth_anchor.seat(sprites, anchor).items():
            layer = group[key]
            image.save(folder / str(layer["url"]), "PNG")
            layer["x"], layer["y"] = int(xy[0]), int(xy[1])
            layer["w"], layer["h"] = image.size
    rest = (view.get("visemes") or {}).get("X")
    if rest is not None:
        sprite = Image.open(folder / str(rest["url"])).convert("RGBA")
        ink = sprite.getchannel("A").point(lambda alpha: 255 if alpha > 128 else 0).getbbox()
        if ink is not None:
            box = [
                int(rest["x"] + ink[0]), int(rest["y"] + ink[1]),
                int(rest["x"] + ink[2]), int(rest["y"] + ink[3]),
            ]
        else:
            box = list(anchor.as_box(face, max(12, int((face[2] - face[0]) * 0.22))))
        qa["mouth"] = box
        qa["mouthBox"] = box
    qa["anchor"] = anchor.to_dict()


def install_true_front(skin: Path) -> dict[str, Any]:
    """Replace ``views/front`` with a midline bust mirrored from the near half.

    A plate that already passes the front gate is left alone. The photographed
    smile stays on the head. Open visemes are moved, not repainted.
    """
    folder = skin / "views" / "front"
    view = _load(folder / "view.json")
    rig = _load(skin / "rig_front.json")
    if view is None or rig is None:
        return {"installed": False, "reason": "no front"}
    boxes = rig.get("boxes") or {}
    before = _symmetry(boxes)
    if before.get("pass"):
        return {
            "installed": False,
            "reason": "already frontal",
            "before": before.get("value"),
            "after": before.get("value"),
        }
    if any(key not in boxes for key in _BOXES):
        return {"installed": False, "reason": "boxes", "before": before.get("value")}
    typed = {key: [int(v) for v in boxes[key]] for key in _BOXES}
    axis = int(round(_axis(typed)))
    face_cx = _centre(typed["face"])
    keep_left = axis <= face_cx
    canvas_w = int((view.get("canvas") or {}).get("w") or 0)
    placed: list[tuple[dict[str, Any], Image.Image, int]] = []
    for layer in view.get("layers") or []:
        if not isinstance(layer, dict):
            continue
        path = folder / str(layer.get("url") or "")
        if not path.is_file():
            continue
        image, origin = symmetrize_sprite(
            Image.open(path).convert("RGBA"), int(layer.get("x") or 0), axis, keep_left=keep_left,
        )
        placed.append((layer, image, origin))
    if not placed:
        return {"installed": False, "reason": "no layers", "before": before.get("value")}
    desired = int(round(face_cx - axis))
    shift = _fit_shift(placed, canvas_w, desired)
    for layer, image, origin in placed:
        layer["x"] = int(origin) + shift
        layer["w"], layer["h"] = image.size
        image.save(folder / str(layer["url"]), "PNG")
    features = _centered_features(typed, axis, shift, keep_left=keep_left)
    # Landmark drift compares vertical placement with the side view. The side
    # mouth and eyes keep their heights; only the horizontal centre moves.
    side = _load(skin / "rig.json") or {}
    side_boxes = side.get("boxes") or {}
    for key in ("left_eye", "right_eye", "mouth", "face"):
        src = side_boxes.get(key) or typed.get(key)
        if isinstance(src, list) and len(src) >= 4:
            features[key][1] = int(src[1])
            features[key][3] = int(src[3])
    rig["boxes"] = {**boxes, **features}
    rig["mouthBox"] = features["mouth"]
    left_mid = _centre(features["left_eye"])
    right_mid = _centre(features["right_eye"])
    if isinstance(rig.get("irises"), list) and len(rig["irises"]) >= 2:
        for iris, mid, box in (
            (rig["irises"][0], left_mid, features["left_eye"]),
            (rig["irises"][1], right_mid, features["right_eye"]),
        ):
            if isinstance(iris, list) and len(iris) >= 2:
                iris[0] = mid
                iris[1] = (int(box[1]) + int(box[3])) / 2
    eye_rig = view.get("eyeRig") or {}
    irises = eye_rig.get("irises")
    if isinstance(irises, list) and len(irises) >= 2:
        for iris, mid, box in (
            (irises[0], left_mid, features["left_eye"]),
            (irises[1], right_mid, features["right_eye"]),
        ):
            if isinstance(iris, list) and len(iris) >= 2:
                iris[0] = mid
                iris[1] = (int(box[1]) + int(box[3])) / 2
    if isinstance(view.get("humanEyes"), dict):
        _map_human(view["humanEyes"], axis, shift, keep_left=keep_left)
    if isinstance(view.get("browModel"), dict):
        view["browModel"] = _map_brow(view["browModel"], axis, shift, keep_left=keep_left)
    for family in ("visemes", "expressions"):
        for layer in (view.get(family) or {}).values():
            if isinstance(layer, dict) and "x" in layer:
                layer["x"] = int(layer["x"]) + shift
    _reseat_mouths(folder, view, features["face"], features["left_eye"], features["right_eye"])
    (folder / "view.json").write_text(json.dumps(view, indent=2), encoding="utf-8")
    (skin / "rig_front.json").write_text(json.dumps(rig, indent=2), encoding="utf-8")
    after = _symmetry(rig["boxes"])
    return {
        "installed": True,
        "before": before.get("value"),
        "after": after.get("value"),
        "pass": bool(after.get("pass")),
        "axis": axis,
        "shift": shift,
        "keepLeft": keep_left,
    }
