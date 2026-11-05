"""Frontal camera from the left three-quarter view. No image call.

The front gate reads detector boxes. A photographed bust fails when the eyes
and the mouth sit off the face-box centre. The left view is the character.
This warps that view so the nose lands on the face centre and the far eye
opens to the near eye's width. Both cheeks stay their own pixels. Left and
right stay the three-quarter plate. The source plate file is not removed.
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


def _monotone(pairs: list[tuple[float, float]]) -> tuple[np.ndarray, np.ndarray]:
    """Source columns in order, destinations never running backwards."""
    ordered = sorted(pairs, key=lambda item: item[0])
    src: list[float] = []
    dst: list[float] = []
    for source, dest in ordered:
        if src and abs(source - src[-1]) < 0.5:
            dst[-1] = max(dst[-1], dest)
            continue
        src.append(float(source))
        dst.append(float(dest) if not dst else max(float(dest), dst[-1] + 0.01))
    return np.asarray(src, np.float64), np.asarray(dst, np.float64)


def control_points(
    boxes: dict[str, list[int]],
    canvas_w: int,
    shift: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Map the three-quarter columns onto a frontal camera.

    The nose moves onto the face centre. The far eye is stretched to the near
    eye's width. Canvas edges stay put, so the plate is not slid off the frame.
    """
    typed = {key: [int(v) for v in boxes[key]] for key in _BOXES}
    axis = int(round(_axis(typed)))
    face_cx = _centre(typed["face"])
    keep_left = axis <= face_cx
    dest = _centered_features(typed, axis, shift, keep_left=keep_left)
    pairs = [
        (0.0, 0.0),
        (float(canvas_w - 1), float(canvas_w - 1)),
        (float(typed["face"][0]), float(dest["face"][0])),
        (float(typed["face"][2]), float(dest["face"][2])),
        (float(typed["left_eye"][0]), float(dest["left_eye"][0])),
        (float(typed["left_eye"][2]), float(dest["left_eye"][2])),
        (float(typed["right_eye"][0]), float(dest["right_eye"][0])),
        (float(typed["right_eye"][2]), float(dest["right_eye"][2])),
        (float(typed["mouth"][0]), float(dest["mouth"][0])),
        (float(typed["mouth"][2]), float(dest["mouth"][2])),
        (float(axis), float(axis + shift)),
    ]
    return _monotone(pairs)


def frontalize_sprite(
    image: Image.Image,
    origin_x: int,
    origin_y: int,
    src_x: np.ndarray,
    dst_x: np.ndarray,
) -> tuple[Image.Image, int, int]:
    """Resample one sprite through the frontal column map. Both sides stay.

    The far cheek is stretched and the near cheek is squeezed. Nothing is
    copied across the nose. ``crop_alpha`` then drops the empty margin, and
    one clear pixel keeps the eyelid inside the edge gate.
    """
    import cv2  # noqa: PLC0415

    from animation.parametric.factory.puppet_assembler import crop_alpha  # noqa: PLC0415

    arr = np.asarray(image.convert("RGBA"))
    height, width = arr.shape[:2]
    if width < 2 or height < 2 or src_x.size < 2:
        return image, int(origin_x), int(origin_y)
    src_left = float(origin_x)
    src_right = float(origin_x + width - 1)
    dst_left = float(np.interp(src_left, src_x, dst_x))
    dst_right = float(np.interp(src_right, src_x, dst_x))
    if dst_right <= dst_left + 1:
        return image, int(origin_x), int(origin_y)
    out_w = max(2, int(round(dst_right - dst_left)) + 1)
    dest_cols = dst_left + np.arange(out_w, dtype=np.float32)
    src_cols = np.interp(dest_cols, dst_x, src_x).astype(np.float32) - np.float32(origin_x)
    map_x = np.broadcast_to(src_cols, (height, out_w)).copy()
    rows = np.arange(height, dtype=np.float32)[:, None]
    map_y = np.broadcast_to(rows, (height, out_w)).copy()
    warped = cv2.remap(
        arr, map_x, map_y, cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0),
    )
    warped[..., 3] = np.where(warped[..., 3] < 16, 0, warped[..., 3])
    ys, xs = np.nonzero(warped[..., 3] > 0)
    if xs.size == 0:
        return image, int(origin_x), int(origin_y)
    cropped = crop_alpha(warped)
    pad = 1
    padded = np.zeros((cropped.shape[0] + 2 * pad, cropped.shape[1] + 2 * pad, 4), np.uint8)
    padded[pad:pad + cropped.shape[0], pad:pad + cropped.shape[1]] = cropped
    new_x = int(round(dst_left)) + int(xs.min()) - pad
    new_y = int(origin_y) + int(ys.min()) - pad
    return Image.fromarray(padded), new_x, new_y


def _slide_to_fit(placed: list[tuple[Image.Image, int]], canvas_w: int) -> int:
    """Slide every sprite by the same amount until each one sits in the frame."""
    if not placed or canvas_w < 2:
        return 0

    def fits(delta: int) -> bool:
        for image, origin in placed:
            x0 = int(origin) + delta
            if x0 < 0 or x0 + image.width > canvas_w:
                return False
        return True

    if fits(0):
        return 0
    for delta in range(1, canvas_w):
        if fits(delta):
            return delta
        if fits(-delta):
            return -delta
    return 0


def _box_at(centre: float, box: list[int], width: int) -> list[int]:
    x0 = int(round(centre - width / 2))
    return [x0, int(box[1]), x0 + int(width), int(box[3])]


def _face_at(face: list[int], centre: float) -> list[int]:
    width = int(face[2]) - int(face[0])
    return _box_at(centre, face, width)


def _remap_brow(
    model: dict[str, Any],
    src_x: np.ndarray,
    dst_x: np.ndarray,
    slide: int,
) -> dict[str, Any]:
    """Move each brow stroke through the same column map as the head."""
    strokes = model.get("strokes") or {}
    out: dict[str, Any] = {}
    for name, stroke in strokes.items():
        if not isinstance(stroke, dict) or not stroke.get("points"):
            out[name] = stroke
            continue
        points = []
        for point in stroke["points"]:
            x = float(np.interp(float(point[0]), src_x, dst_x)) + slide
            points.append([round(x, 2), round(float(point[1]), 2)])
        out[name] = {**stroke, "points": points}
    return {"style": model.get("style") or "illustrated", "strokes": out}


def _remap_human(doc: dict[str, Any], src_x: np.ndarray, dst_x: np.ndarray, slide: int) -> None:
    """Iris and lid boxes follow the frontal column map."""
    iris = doc.get("iris")
    if isinstance(iris, list) and len(iris) >= 2:
        iris[0] = round(float(np.interp(float(iris[0]), src_x, dst_x)) + slide, 2)
    for key in ("sclera", "forehead"):
        box = doc.get(key)
        if isinstance(box, list) and len(box) >= 4:
            box[0] = round(float(np.interp(float(box[0]), src_x, dst_x)) + slide, 2)
            box[2] = round(float(np.interp(float(box[2]), src_x, dst_x)) + slide, 2)


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


def _reference(skin: Path) -> tuple[str, Path, dict[str, Any], dict[str, Any]] | None:
    """The left view is the character the front camera is warped from."""
    folder = skin / "views" / "left"
    view = _load(folder / "view.json")
    rig = _load(skin / "rig.json")
    boxes = (rig or {}).get("boxes") or {}
    if view and rig and all(key in boxes for key in _BOXES):
        return "left", folder, view, rig
    return None


def install_true_front(skin: Path) -> dict[str, Any]:
    """Replace ``views/front`` with a frontal camera warped from the left view.

    A plate that already passes the front gate is left alone. Left and right
    stay three-quarter. The photographed plate file is not deleted. Mouth
    sprites are moved onto the new nose, not repainted.
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
    ref = _reference(skin)
    if ref is None:
        if any(key not in boxes for key in _BOXES):
            return {"installed": False, "reason": "boxes", "before": before.get("value")}
        ref_name, ref_dir, ref_view, ref_boxes = "front", folder, view, boxes
    else:
        ref_name, ref_dir, ref_view, ref_rig = ref
        ref_boxes = ref_rig.get("boxes") or {}
    if any(key not in ref_boxes for key in _BOXES):
        return {"installed": False, "reason": "boxes", "before": before.get("value")}
    canvas = view.get("canvas") or {}
    canvas_w = int(canvas.get("w") or 0)
    ref_w = int((ref_view.get("canvas") or {}).get("w") or 0)
    if canvas_w < 2:
        return {"installed": False, "reason": "canvas", "before": before.get("value")}
    if ref_name != "front" and ref_w not in (0, canvas_w):
        ref_name, ref_dir, ref_view, ref_boxes = "front", folder, view, boxes
    typed = {key: [int(v) for v in ref_boxes[key]] for key in _BOXES}
    axis = int(round(_axis(typed)))
    face_cx = _centre(typed["face"])
    keep_left = axis <= face_cx
    shift = int(round(face_cx - axis))
    src_x, dst_x = control_points(typed, canvas_w, shift)
    ref_layers = {
        layer["name"]: layer
        for layer in (ref_view.get("layers") or [])
        if isinstance(layer, dict) and layer.get("name")
    }
    loaded: list[tuple[dict[str, Any], Image.Image, int, int]] = []
    for layer in view.get("layers") or []:
        if not isinstance(layer, dict):
            continue
        source = ref_layers.get(str(layer.get("name") or "")) or layer
        src_folder = ref_dir if layer.get("name") in ref_layers else folder
        path = src_folder / str(source.get("url") or "")
        if not path.is_file():
            path = folder / str(layer.get("url") or "")
        if not path.is_file():
            continue
        warped, origin_x, origin_y = frontalize_sprite(
            Image.open(path).convert("RGBA"),
            int(source.get("x") or 0),
            int(source.get("y") or 0),
            src_x,
            dst_x,
        )
        loaded.append((layer, warped, origin_x, origin_y))
    if not loaded:
        return {"installed": False, "reason": "no layers", "before": before.get("value")}
    slide = _slide_to_fit([(image, origin_x) for _layer, image, origin_x, _y in loaded], canvas_w)
    for layer, image, origin_x, origin_y in loaded:
        layer["x"] = int(origin_x) + slide
        layer["y"] = int(origin_y)
        layer["w"], layer["h"] = image.size
        image.save(folder / str(layer["url"]), "PNG")
    features = _centered_features(typed, axis, shift, keep_left=keep_left)
    # Landmark drift compares vertical placement with the side view.
    side = _load(skin / "rig.json") or {}
    side_boxes = side.get("boxes") or {}
    for key in ("left_eye", "right_eye", "mouth", "face"):
        src = side_boxes.get(key) or typed.get(key)
        if isinstance(src, list) and len(src) >= 4:
            features[key][1] = int(src[1])
            features[key][3] = int(src[3])
        features[key][0] = int(features[key][0]) + slide
        features[key][2] = int(features[key][2]) + slide
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
    brow_src = ref_view.get("browModel") if isinstance(ref_view.get("browModel"), dict) else view.get("browModel")
    if isinstance(brow_src, dict):
        view["browModel"] = _remap_brow(brow_src, src_x, dst_x, slide)
    human_src = ref_view.get("humanEyes") if isinstance(ref_view.get("humanEyes"), dict) else None
    if isinstance(human_src, dict):
        view["humanEyes"] = json.loads(json.dumps(human_src))
        _remap_human(view["humanEyes"], src_x, dst_x, slide)
    if isinstance(ref_view.get("eyeRig"), dict):
        view["eyeRig"] = json.loads(json.dumps(ref_view["eyeRig"]))
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
    _reseat_mouths(folder, view, features["face"], features["left_eye"], features["right_eye"])
    (folder / "view.json").write_text(json.dumps(view, indent=2), encoding="utf-8")
    (skin / "rig_front.json").write_text(json.dumps(rig, indent=2), encoding="utf-8")
    from . import qa as qa_mod  # noqa: PLC0415

    qa_mod.match_skin_rim(folder)
    after = _symmetry(rig["boxes"])
    return {
        "installed": True,
        "before": before.get("value"),
        "after": after.get("value"),
        "pass": bool(after.get("pass")),
        "axis": axis,
        "shift": shift + slide,
        "reference": ref_name,
        "method": "camera-warp",
    }
