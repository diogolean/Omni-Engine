"""Vector eyebrows for stick and illustrated faces.

The painted brow is a measurement, not a sprite. ``Stroke`` keeps the
centerline from outer tip to inner tip, the thickness at each sample, and
one ink colour. ``STATES`` bends that stroke. Positive lift and inner drop
move the brow down. Evil drops the inner tips, so the pair reads as a V.
Sad lifts them, so the pair reads as an inverse V. Happy lifts the
middle and leaves the tips level, so the pair reads as an arch. A rest
whose inner tip already sits above the outer tip is releveled onto that
arch before it is drawn: the ends stay level and the bow in the middle
stays. The sad pose still lifts the inner tips.

Stick redraws one flat ink width. Illustrated keeps the measured colour,
thickness, and tapered tips. Both are drawn at 4x and scaled down, the same
idea as Aiwake's tapered arch, on a human or stick stroke instead of a robot
bevel.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import cv2
import numpy as np
from PIL import Image, ImageDraw

STICK_INK = (35, 38, 44, 255)
_SUPERSAMPLE = 4
# (lift, inner drop, arch). Arch lifts the middle. The ends stay put.
Pose = tuple[float, float, float]

STATES: dict[str, tuple[Pose, Pose]] = {
    "neutral": ((0.0, 0.0, 0.06), (0.0, 0.0, 0.06)),
    "evil": ((0.02, 0.26, 0.0), (0.02, 0.26, 0.0)),
    # Inner drop stays 0 so the tips do not rise into an inverse V.
    # The arch is strong enough to clear the stick rest curve.
    "happy": ((-0.08, 0.0, 0.36), (-0.08, 0.0, 0.36)),
    "perplexed": ((-0.04, 0.20, 0.02), (-0.02, -0.16, 0.14)),
    "sad": ((0.04, -0.26, 0.02), (0.04, -0.26, 0.02)),
    "angry": ((0.06, 0.18, -0.02), (0.06, 0.18, -0.02)),
    "surprised": ((-0.18, -0.02, 0.22), (-0.18, -0.02, 0.22)),
    "mocking": ((-0.06, -0.14, 0.16), (0.04, 0.18, 0.0)),
    "thoughtful": ((-0.02, 0.10, 0.06), (0.02, -0.04, 0.10)),
}

ALIASES = {
    "neutral": "neutral", "neutro": "neutral",
    "evil": "evil", "mau": "evil", "v": "evil",
    "happy": "happy", "bom": "happy", "good": "happy", "alegre": "happy",
    "perplexed": "perplexed", "perplexo": "perplexed",
    "sad": "sad", "triste": "sad",
    "angry": "angry", "irritado": "angry", "bravo": "angry",
    "surprised": "surprised", "surpreso": "surprised",
    "mocking": "mocking", "desdenhoso": "mocking", "ironico": "mocking",
    "irônico": "mocking", "sceptical": "mocking", "skeptical": "mocking",
    "thoughtful": "thoughtful", "pensativo": "thoughtful", "thinking": "thoughtful",
}

# Keys already stored on scene.EMO_BROW_POSE. Rigs without a stroke model still tilt.
_POSE_KEY = {
    "neutral": "neutro",
    "evil": "irritado",
    "happy": "alegre",
    "perplexed": "desdenhoso",
    "sad": "triste",
    "angry": "irritado",
    "surprised": "surpreso",
    "mocking": "desdenhoso",
    "thoughtful": "pensativo",
}

_STICK_Y = {
    "arch": (180.0, 164.0, 176.0),
    "soft": (178.0, 172.0, 176.0),
    "blade": (174.0, 173.0, 168.0),
}
_STICK_WIDTH = {"arch": 4.0, "soft": 3.2, "blade": 5.5}


@dataclass(frozen=True)
class Stroke:
    """One brow, outer tip first. Thickness is the full width in pixels."""

    points: tuple[tuple[float, float], ...]
    thickness: tuple[float, ...]
    color: tuple[int, int, int, int]

    def to_dict(self) -> dict:
        return {
            "points": [[round(x, 2), round(y, 2)] for x, y in self.points],
            "thickness": [round(t, 2) for t in self.thickness],
            "color": [int(v) for v in self.color],
        }

    @classmethod
    def from_dict(cls, data: dict) -> Stroke:
        points = tuple((float(p[0]), float(p[1])) for p in data["points"])
        thickness = tuple(float(t) for t in data["thickness"])
        color = tuple(int(v) for v in data["color"])
        if len(color) == 3:
            color = (*color, 255)
        return cls(points, thickness, color)  # type: ignore[arg-type]


def resolve(name: str) -> str:
    """Canonical state. Unknown names rest at neutral."""
    return ALIASES.get(str(name or "").strip().lower(), "neutral")


def poses(name: str) -> tuple[Pose, Pose]:
    """``(left, right)`` pose for a speech emotion, including mau/bom/triste."""
    return STATES[resolve(name)]


def pose_key(name: str) -> str:
    """Legacy tilt key. Illustrated and stick rigs that store a model redraw instead."""
    return _POSE_KEY[resolve(name)]


# How far the upper lid travels toward the lower lid. 0 is wide open.
# The still sheet and the scene compositor both read this table.
LID = {
    "neutral": 0.0,
    "evil": 0.46,
    "happy": 0.0,
    "perplexed": 0.30,
    "sad": 0.22,
    "angry": 0.40,
    "surprised": 0.0,
    "mocking": 0.32,
    "thoughtful": 0.18,
}
# Iris offset in gaze units. Happy looks up. Sad and evil look down.
GAZE = {
    "neutral": (0.0, 0.0),
    "evil": (0.2, 0.85),
    "happy": (0.0, -1.0),
    "perplexed": (-0.7, -0.35),
    "sad": (0.0, 1.0),
    "angry": (0.25, 0.45),
    "surprised": (0.0, -0.4),
    "mocking": (0.45, -0.2),
    "thoughtful": (-0.25, -0.15),
}
# Same cut as the painted lid: at this amount the open eye uses the half lid.
SQUINT_AT = 0.20


def lid_amount(name: str) -> float:
    """Upper-lid travel for a speech emotion, including mau, alegre and triste."""
    return LID[resolve(name)]


def eye_lids(name: str) -> str:
    """Blink-open pose. A squint uses the half lid. A blink can still close it."""
    return "half" if lid_amount(name) >= SQUINT_AT else "open"


def gaze_bias(name: str) -> tuple[float, float]:
    """``(x, y)`` added to the look target. Positive y looks down."""
    return GAZE[resolve(name)]


def masks_from_polygons(size: tuple[int, int], polygons: Sequence[Sequence[tuple[int, int]]]) -> list[tuple[np.ndarray, str]]:
    """Raster brow contours. The first polygon is the viewer's left brow."""
    found = []
    for index, poly in enumerate(polygons):
        if poly is None or len(poly) < 2:
            continue
        mask = Image.new("L", size, 0)
        draw = ImageDraw.Draw(mask)
        pts = [(int(p[0]), int(p[1])) for p in poly]
        if len(pts) >= 3:
            draw.polygon(pts, fill=255)
        else:
            draw.line(pts, fill=255, width=4)
        found.append((np.asarray(mask) > 128, "left" if index == 0 else "right"))
    return found


def detect(plate: Image.Image) -> list[tuple[np.ndarray, str]]:
    """Wide dark strokes on the plate. A round iris is not a brow."""
    arr = np.asarray(plate.convert("RGBA"))
    rgb = arr[..., :3].astype(np.float32)
    alpha = arr[..., 3] > 128
    lum = rgb.mean(axis=2)
    opaque = alpha & (lum > 80)
    if int(opaque.sum()) < 20:
        return []
    skin = np.median(rgb[opaque], axis=0)
    dark = alpha & (lum < float(skin.mean()) - 36) & (np.abs(rgb - skin).max(axis=2) > 28)
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(dark.astype(np.uint8), connectivity=8)
    candidates = []
    for index in range(1, count):
        width = int(stats[index, cv2.CC_STAT_WIDTH])
        height = int(stats[index, cv2.CC_STAT_HEIGHT])
        area = int(stats[index, cv2.CC_STAT_AREA])
        if width >= 8 and width >= height * 1.35 and area >= 8:
            candidates.append(index)
    candidates.sort(key=lambda i: int(stats[i, cv2.CC_STAT_AREA]), reverse=True)
    candidates = candidates[:2]
    candidates.sort(key=lambda i: float(centroids[i][0]))
    found = []
    for rank, index in enumerate(candidates):
        if len(candidates) == 1:
            side = "left" if float(centroids[index][0]) < arr.shape[1] / 2 else "right"
        else:
            side = "left" if rank == 0 else "right"
        found.append((labels == index, side))
    return found


def measure(mask: np.ndarray, rgb: np.ndarray, side: str) -> Stroke | None:
    """Centerline, thickness, and median colour. Outer tip is first."""
    ys, xs = np.nonzero(mask)
    if xs.size < 6:
        return None
    horizontal = int(xs.max() - xs.min()) >= int(ys.max() - ys.min())
    samples: list[tuple[float, float, float, tuple[int, int, int, int]]] = []
    count = 21
    if horizontal:
        x0, x1 = int(xs.min()), int(xs.max())
        if x1 <= x0:
            return None
        for index in range(count):
            x = int(round(x0 + (x1 - x0) * index / (count - 1)))
            column = np.nonzero(mask[:, min(x, mask.shape[1] - 1)])[0]
            if column.size == 0:
                continue
            colour = _median_colour(rgb, column, x)
            samples.append((float(x), float(np.median(column)), float(column.max() - column.min() + 1), colour))
        if side == "right":
            samples.reverse()
    else:
        y0, y1 = int(ys.min()), int(ys.max())
        if y1 <= y0:
            return None
        for index in range(count):
            y = int(round(y0 + (y1 - y0) * index / (count - 1)))
            row = np.nonzero(mask[min(y, mask.shape[0] - 1)])[0]
            if row.size == 0:
                continue
            colour = _median_colour(rgb, np.array([y]), int(np.median(row)))
            samples.append((float(np.median(row)), float(y), float(row.max() - row.min() + 1), colour))
    if len(samples) < 4:
        return None
    colours = np.array([sample[3] for sample in samples], dtype=np.int16)
    colour = tuple(int(v) for v in np.median(colours, axis=0))
    return Stroke(
        tuple((sample[0], sample[1]) for sample in samples),
        tuple(sample[2] for sample in samples),
        colour,  # type: ignore[arg-type]
    )


def erase(plate: Image.Image, mask: np.ndarray) -> Image.Image:
    """Inpaint the painted brow so the vector layer is not a second brow."""
    hole = (mask.astype(np.uint8)) * 255
    hole = cv2.dilate(hole, np.ones((5, 5), np.uint8))
    if int(hole.max()) == 0:
        return plate.convert("RGBA")
    rgb = np.ascontiguousarray(np.asarray(plate.convert("RGB")))
    painted = cv2.inpaint(rgb, hole, 5, cv2.INPAINT_TELEA)
    out = np.asarray(plate.convert("RGBA")).copy()
    out[..., :3] = painted
    return Image.fromarray(out)


def draw(stroke: Stroke, pose: Pose, style: str) -> tuple[Image.Image, tuple[int, int]]:
    """Redraw one brow. The origin is in the same space as ``stroke.points``."""
    points = _deform(stroke.points, pose)
    if style == "stick":
        width = max(2.0, min(8.0, float(np.median(stroke.thickness))))
        thickness = tuple(width for _ in points)
        colour = STICK_INK
    else:
        thickness = tuple(max(1.0, float(t)) for t in stroke.thickness)
        colour = stroke.color if len(stroke.color) == 4 else (*stroke.color, 255)
        if len(thickness) != len(points):
            thickness = tuple(thickness[:1] * len(points)) if thickness else tuple(2.0 for _ in points)
    if len(points) < 2:
        return Image.new("RGBA", (1, 1), (0, 0, 0, 0)), (0, 0)
    ribbon = _ribbon(points, thickness)
    scale = _SUPERSAMPLE
    scaled = [(x * scale, y * scale) for x, y in ribbon]
    pad = 4 * scale
    min_x = min(p[0] for p in scaled) - pad
    min_y = min(p[1] for p in scaled) - pad
    shifted = [(p[0] - min_x, p[1] - min_y) for p in scaled]
    width_px = max(1, int(max(p[0] for p in shifted) + pad))
    height_px = max(1, int(max(p[1] for p in shifted) + pad))
    canvas = Image.new("RGBA", (width_px, height_px), (0, 0, 0, 0))
    pen = ImageDraw.Draw(canvas)
    pen.polygon(shifted, fill=colour)
    for (x, y), thick in ((points[0], thickness[0]), (points[-1], thickness[-1])):
        radius = max(0.8, thick / 2) * scale
        cx, cy = x * scale - min_x, y * scale - min_y
        pen.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=colour)
    out = canvas.resize((max(1, canvas.width // scale), max(1, canvas.height // scale)), Image.Resampling.LANCZOS)
    out = _solid_core(out)
    bbox = out.getchannel("A").getbbox()
    origin = (int(round(min_x / scale)), int(round(min_y / scale)))
    if bbox is None:
        return Image.new("RGBA", (1, 1), (0, 0, 0, 0)), origin
    return out.crop(bbox), (origin[0] + bbox[0], origin[1] + bbox[1])


def from_masks(plate: Image.Image, found: Sequence[tuple[np.ndarray, str]], style: str) -> dict:
    """Erase every mask and redraw the strokes that measure cleanly."""
    style = style if style in {"stick", "illustrated"} else "illustrated"
    rgb = np.asarray(plate.convert("RGB"))
    head = plate.convert("RGBA")
    layers = {
        "left": Image.new("RGBA", plate.size, (0, 0, 0, 0)),
        "right": Image.new("RGBA", plate.size, (0, 0, 0, 0)),
    }
    strokes: dict[str, dict] = {}
    neutral = poses("neutral")
    for mask, side in found:
        if side not in layers:
            continue
        stroke = measure(mask, rgb, side)
        head = erase(head, mask)
        if stroke is None:
            painted = plate.convert("RGBA")
            alpha = Image.fromarray((mask.astype(np.uint8)) * 255)
            painted.putalpha(alpha)
            paste(layers[side], painted, (0, 0))
            continue
        pose = neutral[0 if side == "left" else 1]
        sprite, origin = draw(stroke, pose, style)
        paste(layers[side], sprite, origin)
        strokes[side] = stroke.to_dict()
    combined = Image.new("RGBA", plate.size, (0, 0, 0, 0))
    combined.alpha_composite(layers["left"])
    combined.alpha_composite(layers["right"])
    model = {"style": style, "strokes": strokes} if strokes else None
    return {
        "head": head,
        "brow_left": layers["left"],
        "brow_right": layers["right"],
        "brows": combined,
        "model": model,
    }


def lift(plate: Image.Image, *, style: str, polygons: Sequence[Sequence[tuple[int, int]]] | None = None) -> dict:
    """Measure, erase, and redraw. Polygons win over the dark-stroke detector."""
    if polygons is not None:
        found = masks_from_polygons(plate.size, polygons)
    else:
        found = detect(plate)
    return from_masks(plate, found, style)


def render_state(model: dict, state: str, blend: float = 1.0) -> dict[str, tuple[Image.Image, tuple[int, int]]]:
    """One sprite per side for this emotion. ``blend`` eases in from neutral."""
    style = str(model.get("style") or "illustrated")
    left_pose, right_pose = poses(state)
    rest_left, rest_right = poses("neutral")
    weight = max(0.0, min(1.0, float(blend)))
    out = {}
    for side, emotion_pose, rest in (
        ("left", left_pose, rest_left),
        ("right", right_pose, rest_right),
    ):
        raw = (model.get("strokes") or {}).get(side)
        if not raw:
            continue
        pose = _lerp(rest, emotion_pose, weight)
        out[side] = draw(Stroke.from_dict(raw), pose, style)
    return out


def arch_delta(sprite: Image.Image, side: str) -> float:
    """Inner-end y minus outer-end y. Positive means the inner tip sits lower."""
    alpha = np.asarray(sprite.getchannel("A")) > 32
    ys, xs = np.nonzero(alpha)
    if xs.size < 8:
        return 0.0
    x0, x1 = int(xs.min()), int(xs.max())
    third = max(1, (x1 - x0) // 3)

    def median_y(start: int, end: int) -> float | None:
        slab = alpha[:, start:end + 1]
        rows, _cols = np.nonzero(slab)
        if rows.size == 0:
            return None
        return float(np.median(rows))

    left_y = median_y(x0, x0 + third)
    right_y = median_y(x1 - third, x1)
    if left_y is None or right_y is None:
        return 0.0
    outer, inner = (left_y, right_y) if side == "left" else (right_y, left_y)
    return inner - outer


# Middle above both ends, in pixels. Below this a resting stick curve is not an arch.
_HAPPY_RISE = 8.0


def _profile(sprite: Image.Image, side: str) -> tuple[float, float, float] | None:
    """Outer, middle, and inner median y. Smaller y is higher on the sprite."""
    alpha = np.asarray(sprite.getchannel("A")) > 32
    _rows, xs = np.nonzero(alpha)
    if xs.size < 8:
        return None
    x0, x1 = int(xs.min()), int(xs.max())
    third = max(1, (x1 - x0) // 3)

    def median_y(start: int, end: int) -> float | None:
        slab = alpha[:, start:end + 1]
        rows, _cols = np.nonzero(slab)
        if rows.size == 0:
            return None
        return float(np.median(rows))

    left_y = median_y(x0, x0 + third)
    mid_y = median_y(x0 + third, max(x0 + third, x1 - third))
    right_y = median_y(x1 - third, x1)
    if left_y is None or mid_y is None or right_y is None:
        return None
    outer, inner = (left_y, right_y) if side == "left" else (right_y, left_y)
    return outer, mid_y, inner


def _arched(outer: float, mid: float, inner: float) -> bool:
    """The middle is the high point, and it clears the tip-to-tip slope."""
    rise = min(outer, inner) - mid
    return rise >= _HAPPY_RISE and rise >= abs(inner - outer) + 4.0


def classify(left: Image.Image | None, right: Image.Image | None = None) -> str:
    """``happy`` when the middle arches above both ends, ``v`` when every inner tip is down,
    ``inverse`` when every inner tip is up."""
    deltas = []
    profiles = []
    if left is not None and _inked(left):
        deltas.append(arch_delta(left, "left"))
        found = _profile(left, "left")
        if found is not None:
            profiles.append(found)
    if right is not None and _inked(right):
        deltas.append(arch_delta(right, "right"))
        found = _profile(right, "right")
        if found is not None:
            profiles.append(found)
    if not deltas:
        return "flat"
    if profiles and all(_arched(*profile) for profile in profiles):
        return "happy"
    if all(delta > 1.5 for delta in deltas):
        return "v"
    if all(delta < -1.5 for delta in deltas):
        return "inverse"
    return "other"


def orient_stroke(stroke: Stroke) -> Stroke:
    """Level a rest whose inner tip sits above the outer tip.

    The Aiwake arch keeps the ends level and the middle high. Deviation
    from the tip-to-tip chord stays, so a bow remains a bow. A straight
    inverse slope becomes flat. A stroke that is already level is unchanged.
    """
    points = stroke.points
    if len(points) < 2:
        return stroke
    outer_y = float(points[0][1])
    inner_y = float(points[-1][1])
    if inner_y >= outer_y - 1.5:
        return stroke
    last = max(1, len(points) - 1)
    leveled: list[tuple[float, float]] = []
    for index, (x, y) in enumerate(points):
        chord = outer_y + (inner_y - outer_y) * (index / last)
        leveled.append((float(x), outer_y + (float(y) - chord)))
    return Stroke(tuple(leveled), stroke.thickness, stroke.color)


def orient_model(model: dict) -> dict:
    """Relevel every rest stroke. Emotion poses are applied later."""
    strokes = {}
    for side, raw in (model.get("strokes") or {}).items():
        if not isinstance(raw, dict):
            continue
        strokes[side] = orient_stroke(Stroke.from_dict(raw)).to_dict()
    return {**model, "strokes": strokes}


def _flip_stroke(stroke: Stroke) -> Stroke:
    return Stroke(tuple(reversed(stroke.points)), tuple(reversed(stroke.thickness)), stroke.color)


def _rest_kind(model: dict) -> str:
    drawn = render_state(model, "neutral", 1.0)
    left = drawn["left"][0] if "left" in drawn else None
    right = drawn["right"][0] if "right" in drawn else None
    return classify(left, right)


def _layer_map(view: dict) -> dict[str, dict]:
    return {
        str(layer.get("name")): layer
        for layer in (view.get("layers") or [])
        if isinstance(layer, dict) and layer.get("name")
    }


def _local_stroke(stroke: Stroke, layer: dict) -> bool:
    """True when the points live in the sprite, not on the view canvas."""
    ox, oy = int(layer.get("x") or 0), int(layer.get("y") or 0)
    if ox == 0 and oy == 0:
        return False
    width, height = int(layer.get("w") or 0), int(layer.get("h") or 0)
    if width < 2 or height < 2 or not stroke.points:
        return False
    return all(0 <= x <= width + 2 and 0 <= y <= height + 2 for x, y in stroke.points)


def _to_canvas(model: dict, layers: dict[str, dict]) -> dict:
    strokes = {}
    for side, raw in (model.get("strokes") or {}).items():
        if not isinstance(raw, dict):
            continue
        stroke = Stroke.from_dict(raw)
        layer = layers.get(f"brow_{side}") or {}
        if _local_stroke(stroke, layer):
            ox, oy = int(layer.get("x") or 0), int(layer.get("y") or 0)
            stroke = Stroke(
                tuple((x + ox, y + oy) for x, y in stroke.points),
                stroke.thickness,
                stroke.color,
            )
        strokes[side] = stroke.to_dict()
    return {**model, "strokes": strokes}


def _stroke_from_layer(folder, layer: dict, side: str) -> Stroke | None:
    path = folder / str(layer.get("url") or "")
    if not path.is_file():
        return None
    image = Image.open(path).convert("RGBA")
    mask = np.asarray(image.getchannel("A")) > 32
    if int(mask.sum()) < 6:
        return None
    stroke = measure(mask, np.asarray(image.convert("RGB")), side)
    if stroke is None:
        return None
    ox, oy = int(layer.get("x") or 0), int(layer.get("y") or 0)
    return Stroke(tuple((x + ox, y + oy) for x, y in stroke.points), stroke.thickness, stroke.color)


def _model_from_layers(folder, view: dict) -> dict | None:
    layers = _layer_map(view)
    strokes = {}
    for side in ("left", "right"):
        layer = layers.get(f"brow_{side}")
        if layer is None:
            continue
        stroke = _stroke_from_layer(folder, layer, side)
        if stroke is not None:
            strokes[side] = stroke.to_dict()
    if not strokes:
        return None
    return {"style": "illustrated", "strokes": strokes}


def _view_brow_kind(folder, view: dict) -> str | None:
    layers = _layer_map(view)
    images: dict[str, Image.Image] = {}
    for side in ("left", "right"):
        layer = layers.get(f"brow_{side}")
        if layer is None:
            continue
        path = folder / str(layer.get("url") or "")
        if path.is_file():
            images[side] = Image.open(path).convert("RGBA")
    if not images:
        return None
    return classify(images.get("left"), images.get("right"))


def _canvas_size(view: dict) -> tuple[int, int]:
    canvas = view.get("canvas") or {}
    width, height = int(canvas.get("w") or 0), int(canvas.get("h") or 0)
    if width > 1 and height > 1:
        return width, height
    width = height = 1
    for layer in view.get("layers") or []:
        if not isinstance(layer, dict):
            continue
        width = max(width, int(layer.get("x") or 0) + int(layer.get("w") or 0))
        height = max(height, int(layer.get("y") or 0) + int(layer.get("h") or 0))
    return width, height


def _paint_brows(folder, view: dict, model: dict) -> None:
    layers = _layer_map(view)
    width, height = _canvas_size(view)
    style = str(model.get("style") or "illustrated")
    neutral = poses("neutral")
    for index, side in enumerate(("left", "right")):
        raw = (model.get("strokes") or {}).get(side)
        layer = layers.get(f"brow_{side}")
        if not raw or layer is None:
            continue
        sprite, origin = draw(Stroke.from_dict(raw), neutral[index], style)
        plate = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        paste(plate, sprite, origin)
        url = str(layer.get("url") or f"brow_{side}.png")
        path = folder / url
        path.parent.mkdir(parents=True, exist_ok=True)
        plate.save(path)
        layer["x"], layer["y"] = 0, 0
        layer["w"], layer["h"] = width, height
        layer["url"] = url


def repair_orientation(skin) -> list[str]:
    """Redraw a rest brow that classifies as inverse. Sad still lifts the inner tips.

    Uses the stored ``browModel`` when there is one, otherwise the pixels of
    the brow layers. Views that are already not inverse are left alone.
    """
    import json
    from pathlib import Path

    skin = Path(skin)
    changed: list[str] = []
    for view_name in ("front", "left", "right"):
        folder = skin / "views" / view_name
        spec = folder / "view.json"
        if not spec.is_file():
            continue
        try:
            view = json.loads(spec.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(view, dict) or _view_brow_kind(folder, view) != "inverse":
            continue
        layers = _layer_map(view)
        model = view.get("browModel") if isinstance(view.get("browModel"), dict) else None
        if model and (model.get("strokes") or {}):
            model = _to_canvas(model, layers)
        else:
            model = _model_from_layers(folder, view)
        if not model:
            continue
        fixed = orient_model(model)
        if _rest_kind(fixed) == "inverse":
            flipped = {
                "style": fixed.get("style") or "illustrated",
                "strokes": {
                    side: orient_stroke(_flip_stroke(Stroke.from_dict(raw))).to_dict()
                    for side, raw in (fixed.get("strokes") or {}).items()
                    if isinstance(raw, dict)
                },
            }
            if _rest_kind(flipped) != "inverse":
                fixed = flipped
        if _rest_kind(fixed) == "inverse":
            continue
        _paint_brows(folder, view, fixed)
        view["browModel"] = fixed
        spec.write_text(json.dumps(view, indent=2), encoding="utf-8")
        changed.append(view_name)
    return changed


def mirror_model(model: dict, width: int) -> dict:
    """Flip a front model for the mirrored view. Outer stays the first point."""
    strokes = {}
    for side, raw in (model.get("strokes") or {}).items():
        stroke = Stroke.from_dict(raw)
        flipped = Stroke(
            tuple((float(width) - x, y) for x, y in stroke.points),
            stroke.thickness,
            stroke.color,
        )
        strokes["right" if side == "left" else "left"] = flipped.to_dict()
    return {"style": model.get("style") or "illustrated", "strokes": strokes}


def transform_model(model: dict, scale: float, offset: tuple[float, float]) -> dict:
    """Move plate-space strokes onto the seated canvas."""
    ox, oy = offset
    strokes = {}
    for side, raw in (model.get("strokes") or {}).items():
        stroke = Stroke.from_dict(raw)
        strokes[side] = Stroke(
            tuple((x * scale + ox, y * scale + oy) for x, y in stroke.points),
            tuple(max(1.0, t * scale) for t in stroke.thickness),
            stroke.color,
        ).to_dict()
    return {"style": model.get("style") or "illustrated", "strokes": strokes}


def stick_strokes(kind: str, *, width: int = 640) -> tuple[Stroke, Stroke]:
    """Preset stick brows in canvas pixels. Blade, soft, and arch are different strokes."""
    ys = _STICK_Y.get(kind, _STICK_Y["arch"])
    thick = _STICK_WIDTH.get(kind, _STICK_WIDTH["arch"])
    y0, y_mid, y1 = ys
    points = []
    count = 17
    for index in range(count):
        t = index / (count - 1)
        y = y0 * (1 - t) * (1 - 2 * t) + y_mid * 4 * t * (1 - t) + y1 * t * (2 * t - 1)
        x = 248 + (304 - 248) * t
        points.append((x, y))
    left = Stroke(tuple(points), tuple(thick for _ in points), STICK_INK)
    right = Stroke.from_dict(mirror_model({"style": "stick", "strokes": {"left": left.to_dict()}}, width)["strokes"]["right"])
    return left, right


def clamp_origin(origin: tuple[int, int], size: tuple[int, int], forehead: Sequence[float] | None) -> tuple[int, int]:
    """Keep a redrawn brow on the forehead without stretching it."""
    if not forehead or len(forehead) != 4:
        return origin
    x0, y0, x1, y1 = (int(v) for v in forehead)
    return (
        int(min(max(origin[0], x0), max(x0, x1 - size[0]))),
        int(min(max(origin[1], y0), max(y0, y1 - size[1]))),
    )


def _median_colour(rgb: np.ndarray, rows: np.ndarray, x: int) -> tuple[int, int, int, int]:
    x = int(np.clip(x, 0, rgb.shape[1] - 1))
    rows = rows[(rows >= 0) & (rows < rgb.shape[0])]
    if rows.size == 0:
        return STICK_INK
    tone = np.median(rgb[rows, x], axis=0)
    return tuple(int(v) for v in tone) + (255,)  # type: ignore[return-value]


def _deform(points: tuple[tuple[float, float], ...], pose: Pose) -> list[tuple[float, float]]:
    lift, inner, arch = pose
    span = max(8.0, ((points[-1][0] - points[0][0]) ** 2 + (points[-1][1] - points[0][1]) ** 2) ** 0.5)
    last = max(1, len(points) - 1)
    out = []
    for index, (x, y) in enumerate(points):
        t = index / last
        bow = 4 * t * (1.0 - t)
        out.append((x, y + (lift + inner * t - arch * bow) * span))
    return out


def _ribbon(points: Sequence[tuple[float, float]], thickness: Sequence[float]) -> list[tuple[float, float]]:
    upper: list[tuple[float, float]] = []
    lower: list[tuple[float, float]] = []
    last = len(points) - 1
    for index, (x, y) in enumerate(points):
        if index == 0:
            dx, dy = points[1][0] - x, points[1][1] - y
        elif index == last:
            dx, dy = x - points[index - 1][0], y - points[index - 1][1]
        else:
            dx = points[index + 1][0] - points[index - 1][0]
            dy = points[index + 1][1] - points[index - 1][1]
        length = max(1e-6, (dx * dx + dy * dy) ** 0.5)
        nx, ny = -dy / length, dx / length
        half = max(0.6, thickness[min(index, len(thickness) - 1)] / 2)
        upper.append((x + nx * half, y + ny * half))
        lower.append((x - nx * half, y - ny * half))
    return upper + lower[::-1]


def _solid_core(image: Image.Image) -> Image.Image:
    arr = np.asarray(image).copy()
    core = arr[..., 3] > 180
    arr[core, 3] = 255
    if not core.any() and int(arr[..., 3].max()) > 0:
        peak = arr[..., 3] == arr[..., 3].max()
        arr[peak, 3] = 255
    return Image.fromarray(arr)


def paste(canvas: Image.Image, sprite: Image.Image, origin: tuple[int, int]) -> None:
    """Composite ``sprite`` at ``origin``, clipping anything that falls outside ``canvas``."""
    x, y = int(origin[0]), int(origin[1])
    left = max(0, -x)
    top = max(0, -y)
    right = min(sprite.width, canvas.width - x)
    bottom = min(sprite.height, canvas.height - y)
    if right <= left or bottom <= top:
        return
    canvas.alpha_composite(sprite.crop((left, top, right, bottom)), (max(0, x), max(0, y)))


def _lerp(rest: Pose, emotion: Pose, weight: float) -> Pose:
    return tuple(rest[i] + (emotion[i] - rest[i]) * weight for i in range(3))  # type: ignore[return-value]


def _inked(sprite: Image.Image) -> bool:
    return int(np.count_nonzero(np.asarray(sprite.getchannel("A")) > 32)) >= 8
