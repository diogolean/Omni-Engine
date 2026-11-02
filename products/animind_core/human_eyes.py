"""Human eyes and brows cut from a portrait plate.

Aiwake's puppets are robots: a slit optic and a drawn shutter. A person needs
the eye that was actually painted. FaceLandmarker supplies the lid and brow
contours. The blink is skin sampled from just above that lid, so it closes
on the drawn line instead of a grey box. Gaze slides the iris inside the
sclera mask. Brows are cut off the plate and posed with the same emotion as
the mouth.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

import cv2
import numpy as np
from PIL import Image, ImageDraw

from . import eyes, settings

# Script emotions and the English names used on the still sheet.
EMOTIONS = ("neutral", "angry", "sad", "surprised", "sceptical", "thinking")
SCRIPT_EMOTION = {
    "neutral": "neutro",
    "angry": "irritado",
    "sad": "triste",
    "surprised": "surpreso",
    "sceptical": "desdenhoso",
    "thinking": "pensativo",
}
# How far the upper lid travels toward the lower lid. 0 is wide open.
LID_AMOUNT = {
    "neutral": 0.0,
    "angry": 0.40,
    "sad": 0.22,
    "surprised": 0.0,
    "sceptical": 0.32,
    "thinking": 0.18,
}
# (left y, right y, left rotation, right rotation). Positive y is down.
BROW_POSE = {
    "neutral": (0.0, 0.0, 0.0, 0.0),
    "angry": (3.0, 3.0, 14.0, -14.0),
    "sad": (1.5, 1.5, 8.0, -8.0),
    "surprised": (-4.0, -4.0, -2.0, 2.0),
    "sceptical": (-3.0, 2.0, -10.0, 6.0),
    "thinking": (-2.0, 1.0, -6.0, 4.0),
}
MOUTH = {
    "neutral": "neutral",
    "angry": "angry",
    "sad": "sad",
    "surprised": "surprised",
    "sceptical": "ironic",
    "thinking": "thoughtful",
}
BLINK_STATES = ("open", "half", "closed")
_BLINK_AMOUNT = {"open": 0.0, "half": 0.5, "closed": 1.0}


def sample_skin(image: Image.Image, upper: Sequence[tuple[int, int]]) -> tuple[int, int, int]:
    """Median warm skin just above the upper lid. Never a flat grey."""
    arr = np.asarray(image.convert("RGBA"))
    xs = [int(p[0]) for p in upper]
    ys = [int(p[1]) for p in upper]
    y1 = max(1, min(ys) - 2)
    y0 = max(0, y1 - 10)
    x0, x1 = max(0, min(xs) - 2), min(arr.shape[1], max(xs) + 3)
    patch = arr[y0:y1, x0:x1]
    if patch.size == 0:
        return (232, 186, 156)
    rgb = patch[:, :, :3].astype(np.int16)
    opaque = patch[:, :, 3] > 128
    chroma = rgb.max(axis=2) - rgb.min(axis=2)
    warm = opaque & (rgb[:, :, 0] > rgb[:, :, 2] + 6) & (rgb[:, :, 1] > 40) & (chroma > 8)
    if int(warm.sum()) < 6:
        warm = opaque & (rgb.mean(axis=2) > 40) & (rgb.mean(axis=2) < 250)
    if int(warm.sum()) < 4:
        return (232, 186, 156)
    tone = np.median(rgb[warm], axis=0)
    return tuple(int(v) for v in tone)


def _resample(poly: Sequence[tuple[int, int]], n: int = 16) -> list[tuple[int, int]]:
    pts = [(float(x), float(y)) for x, y in poly]
    if len(pts) < 2:
        return [(int(pts[0][0]), int(pts[0][1]))] * n if pts else []
    step = (len(pts) - 1) / max(1, n - 1)
    out = []
    for i in range(n):
        t = i * step
        a = int(t)
        b = min(len(pts) - 1, a + 1)
        f = t - a
        out.append((int(round(pts[a][0] * (1 - f) + pts[b][0] * f)), int(round(pts[a][1] * (1 - f) + pts[b][1] * f))))
    return out


def lowered_lid(upper: Sequence[tuple[int, int]], lower: Sequence[tuple[int, int]], amount: float) -> list[tuple[int, int]]:
    """The drawn upper lid, moved ``amount`` of the way down to the lower lid."""
    up = _resample(upper)
    low = _resample(lower)
    amount = max(0.0, min(1.0, float(amount)))
    return [
        (a[0], int(round(a[1] + (b[1] - a[1]) * amount)))
        for a, b in zip(up, low)
    ]


def lid_line_y(upper: Sequence[tuple[int, int]], lower: Sequence[tuple[int, int]], amount: float, x: int | None = None) -> int:
    """Y of the moving lid at the eye's horizontal centre."""
    curve = lowered_lid(upper, lower, amount)
    if not curve:
        return 0
    if x is None:
        x = int(round(sum(p[0] for p in curve) / len(curve)))
    return min(curve, key=lambda p: abs(p[0] - x))[1]


def paint_lid(
    size: tuple[int, int],
    upper: Sequence[tuple[int, int]],
    lower: Sequence[tuple[int, int]],
    amount: float,
    skin: tuple[int, int, int],
) -> Image.Image:
    """Skin from the drawn upper lid down to the blink line. Empty when open."""
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    if amount <= 0.02:
        return image
    top = _resample(upper)
    bottom = lowered_lid(upper, lower, amount)
    if len(top) < 2 or len(bottom) < 2:
        return image
    poly = top + list(reversed(bottom))
    draw = ImageDraw.Draw(image)
    draw.polygon(poly, fill=(*skin, 255))
    # One soft pixel on the lash line so the edge is the lid, not a hard box.
    draw.line(bottom, fill=(*tuple(max(0, c - 35) for c in skin), 255), width=1)
    return image


def opening_mask(size: tuple[int, int], upper: Sequence[tuple[int, int]], lower: Sequence[tuple[int, int]]) -> Image.Image:
    """The sclera window: upper lid plus lower lid, nothing outside it."""
    mask = Image.new("L", size, 0)
    poly = list(_resample(upper)) + list(reversed(_resample(lower)))
    if len(poly) >= 3:
        ImageDraw.Draw(mask).polygon(poly, fill=255)
    return mask


def cut_eye(
    plate: Image.Image,
    upper: Sequence[tuple[int, int]],
    lower: Sequence[tuple[int, int]],
    iris_xy: tuple[int, int],
    iris_radius: int,
) -> dict[str, Any]:
    """Iris, sclera and lid layers for one drawn eye."""
    size = plate.size
    skin = sample_skin(plate, upper)
    mask = opening_mask(size, upper, lower)
    rgba = np.asarray(plate.convert("RGBA")).copy()
    alpha = np.asarray(mask)
    sclera = rgba.copy()
    sclera[:, :, 3] = np.minimum(sclera[:, :, 3], alpha)
    radius = max(2, int(iris_radius))
    iris = Image.new("RGBA", size, (0, 0, 0, 0))
    box = (iris_xy[0] - radius, iris_xy[1] - radius, iris_xy[0] + radius, iris_xy[1] + radius)
    crop = plate.convert("RGBA").crop(box)
    disc = Image.new("L", crop.size, 0)
    ImageDraw.Draw(disc).ellipse((1, 1, crop.width - 2, crop.height - 2), fill=255)
    crop.putalpha(disc)
    iris.paste(crop, (box[0], box[1]), crop)
    half = paint_lid(size, upper, lower, 0.5, skin)
    closed = paint_lid(size, upper, lower, 1.0, skin)
    center_x = int(round(sum(p[0] for p in _resample(upper)) / max(1, len(_resample(upper)))))
    return {
        "sclera": Image.fromarray(sclera),
        "iris": iris,
        "opening": mask,
        "half": half,
        "closed": closed,
        "skin": skin,
        "upper": [list(p) for p in upper],
        "lower": [list(p) for p in lower],
        "irisXY": [int(iris_xy[0]), int(iris_xy[1])],
        "radius": radius,
        "centerX": center_x,
        "lines": {state: lid_line_y(upper, lower, _BLINK_AMOUNT[state], center_x) for state in BLINK_STATES},
    }


def _mask_rgba(mask: Image.Image) -> Image.Image:
    image = Image.new("RGBA", mask.size, (255, 255, 255, 0))
    image.putalpha(mask)
    return image


def cut_brow(plate: Image.Image, polygon: Sequence[tuple[int, int]]) -> tuple[Image.Image, Image.Image]:
    """Lift the brow off the plate and fill the hole with the surrounding skin."""
    size = plate.size
    mask = Image.new("L", size, 0)
    if len(polygon) >= 3:
        ImageDraw.Draw(mask).polygon(list(polygon), fill=255)
    brow = plate.convert("RGBA")
    brow.putalpha(mask)
    rgb = np.ascontiguousarray(np.asarray(plate.convert("RGB")))
    hole = np.asarray(mask)
    painted = cv2.inpaint(rgb, hole, 3, cv2.INPAINT_TELEA)
    filled = plate.convert("RGBA").copy()
    filled.paste(Image.fromarray(painted), (0, 0))
    return brow, filled


def _almond(center: tuple[int, int], radius: int) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    cx, cy = center
    rx = max(6, int(radius * 2.4))
    ry = max(3, int(radius * 1.1))
    upper, lower = [], []
    for i in range(9):
        t = i / 8
        x = int(round(cx - rx + 2 * rx * t))
        arch = int(round(ry * (1 - (2 * t - 1) ** 2)))
        upper.append((x, cy - arch))
        lower.append((x, cy + int(round(arch * 0.72))))
    return upper, lower


def _eyes_from_marks(marks: Any) -> list[dict[str, Any]]:
    eyes_out = []
    pairs = (
        (marks.left_eye, getattr(marks, "left_upper", ()), getattr(marks, "left_lower", ())),
        (marks.right_eye, getattr(marks, "right_upper", ()), getattr(marks, "right_lower", ())),
    )
    for eye, upper, lower in pairs:
        center = marks.pixel_point(eye.center)
        radius = max(3, int(round(eye.iris_radius * max(8, marks.head_bbox[2] - marks.head_bbox[0]))))
        if len(upper) >= 3 and len(lower) >= 3:
            up, low = list(upper), list(lower)
        else:
            up, low = _almond(center, radius)
        eyes_out.append({"upper": up, "lower": low, "iris": center, "radius": radius})
    return eyes_out


def _brows_from_marks(marks: Any, eyes_geom: list[dict[str, Any]]) -> list[list[tuple[int, int]]]:
    stored = (getattr(marks, "left_brow", ()), getattr(marks, "right_brow", ()))
    if all(len(poly) >= 3 for poly in stored):
        return [list(poly) for poly in stored]
    brows = []
    for eye in eyes_geom:
        ys = [p[1] for p in eye["upper"]]
        xs = [p[0] for p in eye["upper"]]
        top = min(ys) - max(4, eye["radius"])
        brows.append([(min(xs), top + 2), ((min(xs) + max(xs)) // 2, top - 2), (max(xs), top + 2), (max(xs) - 2, top + 5), (min(xs) + 2, top + 5)])
    return brows


def build(plate: Image.Image, marks: Any | None = None, *, eyes_geom: list[dict[str, Any]] | None = None,
          brows: list[Sequence[tuple[int, int]]] | None = None) -> dict[str, Any]:
    """Cut both eyes and both brows. ``marks`` supplies FaceLandmarker contours when present."""
    if eyes_geom is None:
        if marks is None:
            raise ValueError("eyes need landmarks or explicit contours")
        eyes_geom = _eyes_from_marks(marks)
    if brows is None:
        brows = _brows_from_marks(marks, eyes_geom) if marks is not None else []
    size = plate.size
    cut = [cut_eye(plate, eye["upper"], eye["lower"], tuple(eye["iris"]), int(eye["radius"])) for eye in eyes_geom]
    sclera = Image.new("RGBA", size, (0, 0, 0, 0))
    iris = Image.new("RGBA", size, (0, 0, 0, 0))
    socket = Image.new("RGBA", size, (255, 255, 255, 0))
    half = Image.new("RGBA", size, (0, 0, 0, 0))
    closed = Image.new("RGBA", size, (0, 0, 0, 0))
    for one in cut:
        sclera.alpha_composite(one["sclera"])
        iris.alpha_composite(one["iris"])
        socket.alpha_composite(_mask_rgba(one["opening"]))
        half.alpha_composite(one["half"])
        closed.alpha_composite(one["closed"])
    head = plate.convert("RGBA")
    brow_layers = []
    for poly in brows:
        layer, head = cut_brow(head, poly)
        brow_layers.append(layer)
    while len(brow_layers) < 2:
        brow_layers.append(Image.new("RGBA", size, (0, 0, 0, 0)))
    combined = Image.new("RGBA", size, (0, 0, 0, 0))
    for layer in brow_layers:
        combined.alpha_composite(layer)
    painted = {}
    for state, amount in _BLINK_AMOUNT.items():
        # Emotion squint is a separate pose; the blink states are the sheet columns.
        ys = [lid_line_y(one["upper"], one["lower"], amount, one["centerX"]) for one in cut]
        measured = []
        sprite = {"open": Image.new("RGBA", size, (0, 0, 0, 0)), "half": half, "closed": closed}[state]
        for one in cut:
            measured.append(_alpha_edge_y(sprite, one["centerX"], one["lines"][state]))
        painted[state] = {"drawn": ys, "edge": measured}
    eye_top = min(min(p[1] for p in one["upper"]) for one in cut)
    brow_bottom = max((max(p[1] for p in poly) for poly in brows if len(poly)), default=eye_top - 4)
    return {
        "head": head,
        "eye_white": sclera,
        "iris": iris,
        "eye_socket": socket,
        "eyes_half": half,
        "eyes_closed": closed,
        "brows": combined,
        "brow_left": brow_layers[0],
        "brow_right": brow_layers[1],
        "skin": cut[0]["skin"] if cut else (232, 186, 156),
        "qa": {
            "painted": painted,
            "iris": [one["irisXY"] + [one["radius"]] for one in cut],
            "eyeTop": int(eye_top),
            "browBottom": int(brow_bottom),
            "shift": [max(1, min(one["radius"] for one in cut) // 2), max(1, min(one["radius"] for one in cut) // 3)] if cut else [1, 1],
        },
        "emotions": {
            name: {"brow": list(BROW_POSE[name]), "lid": LID_AMOUNT[name], "mouth": MOUTH[name], "script": SCRIPT_EMOTION[name]}
            for name in EMOTIONS
        },
    }


def _alpha_edge_y(sprite: Image.Image, x: int, fallback: int) -> int:
    alpha = np.asarray(sprite.getchannel("A"))
    x = int(np.clip(x, 0, alpha.shape[1] - 1))
    column = np.nonzero(alpha[:, x] > 128)[0]
    if column.size == 0:
        return int(fallback)
    return int(column.max())


def install(skin: Path, plate: Image.Image, marks: Any | None = None) -> dict[str, Any]:
    """Cut the plate and write ``human_eyes/`` next to the puppet. No image call."""
    built = build(plate, marks)
    save(skin, built)
    return built


def save(skin: Path, built: dict[str, Any]) -> None:
    dest = skin / "human_eyes"
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("head", "eye_white", "iris", "eye_socket", "eyes_half", "eyes_closed", "brows", "brow_left", "brow_right"):
        built[name].save(dest / f"{name}.png")
    (dest / "geometry.json").write_text(json.dumps({"qa": built["qa"], "emotions": built["emotions"]}, indent=2), encoding="utf-8")


def load(skin: Path) -> dict[str, Any] | None:
    dest = skin / "human_eyes" / "geometry.json"
    if not dest.is_file():
        return None
    data = json.loads(dest.read_text(encoding="utf-8"))
    folder = dest.parent
    layers = {name: Image.open(folder / f"{name}.png").convert("RGBA") for name in
              ("head", "eye_white", "iris", "eye_socket", "eyes_half", "eyes_closed", "brows", "brow_left", "brow_right")
              if (folder / f"{name}.png").is_file()}
    data["layers"] = layers
    return data


def lids_near_line(painted_y: int, drawn_y: int, tol: int = 4) -> bool:
    """The painted lash sits on the drawn lid line."""
    return abs(int(painted_y) - int(drawn_y)) <= tol


def iris_inside_sclera(iris: Sequence[float], sclera: Sequence[float]) -> bool:
    """``iris`` is (x, y, radius). ``sclera`` is the opening box (x0, y0, x1, y1)."""
    x, y, radius = (float(v) for v in iris)
    x0, y0, x1, y1 = (float(v) for v in sclera)
    return x0 + radius - 1 <= x <= x1 - radius + 1 and y0 + radius - 1 <= y <= y1 - radius + 1


def brow_above_eye(brow_bottom: float, eye_top: float) -> bool:
    return float(brow_bottom) < float(eye_top)


def frame_faults(sample: dict[str, Any], *, tol: int = 4) -> list[str]:
    """One frame of one face. Empty when the lids, iris and brows are seated."""
    faults = []
    if not lids_near_line(sample["lidY"], sample["lineY"], tol):
        faults.append(f"lid {sample['lidY']}px off the drawn line {sample['lineY']}px")
    if not iris_inside_sclera(sample["iris"], sample["sclera"]):
        faults.append("iris outside the sclera")
    if not brow_above_eye(sample["browBottom"], sample["eyeTop"]):
        faults.append("brow crosses the eye")
    return faults


def render_still_sheet(skin: Path, dest: Path | None = None) -> Path:
    """Every emotion by every blink, from files already on disk. No image call."""
    plate_path = next(
        (skin / name for name in ("character.png", "_plate.png", "_gemini.png") if (skin / name).is_file()),
        None,
    )
    if plate_path is None:
        raise FileNotFoundError(f"no cached plate in {skin}")
    plate = Image.open(plate_path).convert("RGBA")
    built = load(skin)
    if built is None:
        from animation.parametric.factory.landmark_detector import detect_landmarks  # noqa: PLC0415

        try:
            marks = detect_landmarks(plate, require_human_face=True)
        except Exception:
            marks = None
        rig_path = skin / "rig.json"
        if marks is None and rig_path.is_file():
            boxes = json.loads(rig_path.read_text(encoding="utf-8"))["boxes"]
            eyes_geom = []
            for key in ("left_eye", "right_eye"):
                box = boxes[key]
                center = ((box[0] + box[2]) // 2, (box[1] + box[3]) // 2)
                upper, lower = _almond(center, max(4, (box[2] - box[0]) // 4))
                eyes_geom.append({"upper": upper, "lower": lower, "iris": center, "radius": max(3, (box[2] - box[0]) // 6)})
            built_raw = build(plate, eyes_geom=eyes_geom)
        else:
            built_raw = build(plate, marks)
        save(skin, built_raw)
        built = load(skin)
    assert built is not None
    layers = built["layers"]
    emotions = built["emotions"]
    cell_w, cell_h = 200, 240
    sheet = Image.new("RGB", (cell_w * len(EMOTIONS), cell_h * len(BLINK_STATES) + 28), (24, 24, 28))
    draw = ImageDraw.Draw(sheet)
    for col, name in enumerate(EMOTIONS):
        spec = emotions[name]
        pose = spec["brow"]
        for row, state in enumerate(BLINK_STATES):
            amount = max(_BLINK_AMOUNT[state], float(spec["lid"]) if state == "open" else _BLINK_AMOUNT[state])
            cell = Image.new("RGBA", plate.size, (0, 0, 0, 255))
            cell.alpha_composite(layers.get("head", plate))
            cell.alpha_composite(layers["eye_white"])
            cell.alpha_composite(layers["iris"])
            lid = paint_amount(layers, amount)
            if lid is not None:
                cell.alpha_composite(lid)
            for layer, dy, angle in (
                (layers["brow_left"], pose[0], pose[2]),
                (layers["brow_right"], pose[1], pose[3]),
            ):
                turned = layer.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True)
                cell.alpha_composite(turned, ((layer.width - turned.width) // 2, (layer.height - turned.height) // 2 + int(dy)))
            thumb = cell.convert("RGB")
            thumb.thumbnail((cell_w - 8, cell_h - 36))
            x = col * cell_w + (cell_w - thumb.width) // 2
            y = 28 + row * cell_h + 4
            sheet.paste(thumb, (x, y))
            draw.text((col * cell_w + 6, 6), name, fill=(240, 240, 240))
            draw.text((col * cell_w + 6, y + thumb.height + 2), f"{state} / {spec['mouth']}", fill=(180, 180, 180))
    out = dest or (skin / "emotion_sheet.png")
    sheet.save(out)
    return out


def paint_amount(layers: dict[str, Image.Image], amount: float) -> Image.Image | None:
    if amount >= 0.85 and "eyes_closed" in layers:
        return layers["eyes_closed"]
    if amount >= 0.2 and "eyes_half" in layers:
        return layers["eyes_half"]
    return None


def main(argv: list[str] | None = None) -> None:
    """Still sheet for a cached character. No image call.

    ``python -m products.animind_core.human_eyes sheet einstein``
    """
    import argparse

    parser = argparse.ArgumentParser(prog="python -m products.animind_core.human_eyes")
    parser.add_argument("command", nargs="?", default="sheet")
    parser.add_argument("name", nargs="?", default="einstein")
    args = parser.parse_args(argv)
    folder = "albert_einstein" if args.name.lower() in {"einstein", "albert", "albert-einstein", "albert_einstein"} else args.name
    skin = settings.data_dir() / "assets" / "puppets" / folder
    path = render_still_sheet(skin)
    print(f"still sheet {path}")


if __name__ == "__main__":
    main()


# Blink timing lives in eyes.py and is the clock this module paints to.
assert eyes.BLINK_MIN == 3.0 and eyes.BLINK_CLOSE_S == 0.12
