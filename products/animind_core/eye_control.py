"""Prove that a view can move its iris without touching the rest of the rig.

A view is ``ready`` only after center, left, right, up, and down composites
differ inside the eye opening and match outside it. A repeated frame or a
shifted head stays ``unavailable``. This module does not call an image provider
and does not rewrite view, mouth, or expression files.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

SCHEMA = "animind.eyeControl.v1"
GAZES = ("center", "left", "right", "up", "down")
_DIRECTIONS = {
    "left": ("x", -1, 0),
    "right": ("x", 1, 0),
    "up": ("y", -1, 1),
    "down": ("y", 1, 1),
}
_NEEDED = {"head", "eye_white", "iris", "eye_socket"}


def probe_view(view: dict[str, Any]) -> dict[str, Any]:
    """Composite one view at five gazes and judge the pixels."""
    layers = view.get("layers") if isinstance(view.get("layers"), list) else None
    names = {str(layer.get("name")) for layer in layers or [] if isinstance(layer, dict)}
    if not layers or not _NEEDED <= names:
        return _unavailable("layers-missing")
    shift = _shift(view)
    try:
        samples = {name: _render(view, *_gaze(name)) for name in GAZES}
    except (OSError, KeyError, TypeError, ValueError):
        return _unavailable("probe-error")
    return judge(samples, _socket(view), shift)


def judge(
    samples: dict[str, tuple[Image.Image, tuple[float, float] | None]],
    socket: tuple[Any, ...],
    shift: tuple[float, float],
) -> dict[str, Any]:
    """Judge labeled frames. ``samples`` maps a gaze to ``(frame, iris centroid)``."""
    frames = {name: samples[name][0] for name in GAZES if name in samples}
    centroids = {name: samples[name][1] for name in GAZES if name in samples}
    if set(frames) != set(GAZES):
        return _unavailable("probe-error")
    if shift[0] == 0 and shift[1] == 0:
        return _unavailable("no-travel", _blank_probes())
    if _same(frames):
        return _unavailable("repeated-image", _blank_probes())
    if not _outside_equal(frames, socket):
        return _unavailable("identity-lost", _blank_probes())
    base = centroids.get("center")
    probes: dict[str, Any] = {"center": {"pass": base is not None, "delta": 0.0}}
    ready = base is not None
    for name, (axis, sign, index) in _DIRECTIONS.items():
        point = centroids.get(name)
        delta = None if point is None or base is None else (point[0] - base[0] if axis == "x" else point[1] - base[1])
        need = max(2.0, 0.35 * abs(shift[index]))
        passed = delta is not None and sign * delta >= need
        probes[name] = {"pass": passed, "delta": None if delta is None else round(float(delta), 2)}
        ready = ready and passed
    if not ready:
        return _unavailable("off-axis", probes)
    return {"status": "ready", "reason": None, "probes": probes}


def assess_skin(skin: Path) -> dict[str, Any]:
    """Read saved views. Missing or incomplete views stay unavailable."""
    views: dict[str, Any] = {}
    for name in ("front", "left", "right"):
        path = skin / "views" / name / "view.json"
        if not path.is_file():
            views[name] = _unavailable("view-missing")
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            views[name] = _unavailable("probe-error")
            continue
        layers = data.get("layers") if isinstance(data, dict) else None
        if not isinstance(layers, list) or not layers:
            views[name] = _unavailable("layers-missing")
            continue
        for layer in layers:
            if isinstance(layer, dict) and layer.get("url"):
                url = Path(str(layer["url"]))
                if not url.is_absolute():
                    layer["url"] = str(path.parent / url)
        views[name] = probe_view(data)
    ready = bool(views) and all(item.get("status") == "ready" for item in views.values())
    return {"schema": SCHEMA, "version": 1, "ready": ready, "views": views}


def upgrade_character(character_id: str) -> dict[str, Any]:
    """Write only ``eye_control.json``. Original views and expressions stay byte-identical."""
    from .characters import puppets_dir  # noqa: PLC0415

    if not re.fullmatch(r"[a-z0-9_]{1,48}", character_id or ""):
        raise FileNotFoundError(character_id)
    skin = puppets_dir() / character_id
    if not (skin / "character.json").is_file():
        raise FileNotFoundError(character_id)
    before = _digest(skin)
    report = assess_skin(skin)
    sidecar = skin / "eye_control.json"
    sidecar.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if _digest(skin) != before:
        sidecar.unlink(missing_ok=True)
        report = {**report, "ready": False, "reason": "upgrade-aborted"}
    return report


def _digest(skin: Path) -> dict[str, bytes]:
    return {
        path.relative_to(skin).as_posix(): path.read_bytes()
        for path in skin.rglob("*")
        if path.is_file() and path.name != "eye_control.json"
    }


def _render(view: dict[str, Any], gx: float, gy: float) -> tuple[Image.Image, tuple[float, float] | None]:
    from .scene import Scene  # noqa: PLC0415

    width, height = int(view["canvas"]["w"]), int(view["canvas"]["h"])
    stage = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    parts = []
    for layer in view["layers"]:
        image = Image.open(layer["url"]).convert("RGBA")
        box = (max(1, int(layer["w"])), max(1, int(layer["h"])))
        if image.size != box:
            image = image.resize(box, Image.Resampling.NEAREST)
        parts.append((str(layer["name"]), image, (int(layer["x"]), int(layer["y"])), int(layer["z"])))
    socket = next(part for part in parts if part[0] == "eye_socket")
    names = {part[0] for part in parts}
    movable = {"eye_white", "iris", "eye_socket"} <= names
    centroid = None
    for name, image, xy, _z in sorted(parts, key=lambda part: part[3]):
        if name in {"eye_socket", "eyes_half", "eyes_closed"} or (movable and name == "eyes"):
            continue
        if name == "iris":
            image, xy = Scene._iris(None, image, xy, socket, _shift(view), gx, gy)
            centroid = _centroid(image, xy)
        stage.alpha_composite(image, xy)
    return stage, centroid


def _centroid(image: Image.Image, xy: tuple[int, int]) -> tuple[float, float] | None:
    alpha = np.asarray(image.getchannel("A"))
    ys, xs = np.nonzero(alpha > 16)
    if xs.size == 0:
        return None
    return float(xs.mean() + xy[0]), float(ys.mean() + xy[1])


def _gaze(name: str) -> tuple[float, float]:
    return {"center": (0.0, 0.0), "left": (-1.0, 0.0), "right": (1.0, 0.0), "up": (0.0, -1.0), "down": (0.0, 1.0)}[name]


def _shift(view: dict[str, Any]) -> tuple[float, float]:
    raw = (view.get("eyeRig") or {}).get("maxShift") or (0, 0)
    return float(raw[0]), float(raw[1])


def _socket(view: dict[str, Any]) -> tuple[Any, ...]:
    for layer in view["layers"]:
        if layer.get("name") == "eye_socket":
            image = Image.open(layer["url"]).convert("RGBA")
            box = (max(1, int(layer["w"])), max(1, int(layer["h"])))
            if image.size != box:
                image = image.resize(box, Image.Resampling.NEAREST)
            return ("eye_socket", image, (int(layer["x"]), int(layer["y"])), int(layer["z"]))
    raise KeyError("eye_socket")


def _same(frames: dict[str, Image.Image]) -> bool:
    center = frames["center"].tobytes()
    return all(frame.size == frames["center"].size and frame.tobytes() == center for frame in frames.values())


def _outside_equal(frames: dict[str, Image.Image], socket: tuple[Any, ...]) -> bool:
    center = np.asarray(frames["center"].convert("RGBA"))
    mask = _outside_mask(frames["center"].size, socket)
    for name, frame in frames.items():
        if name == "center":
            continue
        other = np.asarray(frame.convert("RGBA"))
        if other.shape != center.shape or np.any(center[mask] != other[mask]):
            return False
    return True


def _outside_mask(size: tuple[int, int], socket: tuple[Any, ...]) -> np.ndarray:
    _name, image, (left, top), _z = socket
    mask = np.ones((size[1], size[0]), dtype=bool)
    alpha = np.asarray(image.getchannel("A"))
    height, width = alpha.shape
    x0, y0 = max(0, int(left)), max(0, int(top))
    x1, y1 = min(size[0], int(left) + width), min(size[1], int(top) + height)
    if x1 <= x0 or y1 <= y0:
        return mask
    crop = alpha[y0 - int(top):y0 - int(top) + (y1 - y0), x0 - int(left):x0 - int(left) + (x1 - x0)]
    region = mask[y0:y1, x0:x1]
    region[crop > 16] = False
    return mask


def _blank_probes() -> dict[str, Any]:
    return {name: {"pass": False, "delta": None} for name in GAZES}


def _unavailable(reason: str, probes: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"status": "unavailable", "reason": reason, "probes": probes if probes is not None else {}}
