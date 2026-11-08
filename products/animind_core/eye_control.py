"""Pixel proof for a controllable iris, in the shape the Animind web already checks.

The web contract is ``docs/EYE_RIG.md`` on ``feat/mvp-ui``. Its implementation
in ``lib/eye-rig.ts`` starts the fitted travel at ``[8, 4]`` and shrinks until
75% of the iris stays inside the socket. This module does the same. The view's
published ``eyeRig.maxShift`` stays untouched for the existing renderer.

A view file that was not drawn is omitted. ``gaze`` is ``separable`` only
after the pupil centroid moves left, right, up, and down and the pixels
outside the socket mask stay put. Nothing here calls an image provider or
rewrites view, mouth, or expression files.
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
# lib/eye-rig.ts reportSeparable. The markdown also mentions maxShift; the
# code that the app runs starts here and does not read the published shift.
_PROOF_TRAVEL = (8, 4)
_ALPHA = 32
_PLATE = ("eye_white", "eyes")


def probe_view(view: dict[str, Any]) -> dict[str, Any]:
    """Prove one supplied view. Missing iris or socket layers are unavailable."""
    layers = [layer for layer in (view.get("layers") or []) if isinstance(layer, dict)]
    names = {str(layer.get("name")) for layer in layers}
    if "iris" not in names or "eye_socket" not in names or not (names & set(_PLATE)):
        return _unavailable("this view has no separate iris and socket")
    try:
        loaded = _load(layers)
        plate_name = "eye_white" if "eye_white" in loaded else "eyes"
        plate, iris, socket = loaded[plate_name], loaded["iris"], loaded["eye_socket"]
        rest_x = iris["x"] - socket["x"]
        rest_y = iris["y"] - socket["y"]
        travel = fit_travel(socket["image"], iris["image"], rest_x, rest_y, _PROOF_TRAVEL)
        if travel is None:
            return _unavailable("the socket cannot hold a visible iris shift")
        base = _base_frame(view, loaded)
        samples = {}
        for name in GAZES:
            gx, gy = _gaze(name)
            offset = (int(round(gx * travel[0])), int(round(gy * travel[1])))
            eye = _compose(plate, iris, socket, offset)
            frame = _paste_socket(base, eye, socket)
            samples[name] = (frame, _pupil(frame, socket))
    except (OSError, KeyError, TypeError, ValueError):
        return _unavailable("the iris or socket bytes are missing")
    report = judge(samples, socket)
    if report["gaze"] == "separable":
        report["travel"] = [travel[0], travel[1]]
        report["sockets"] = [_socket_box(socket)]
    return report


def judge(
    samples: dict[str, tuple[Image.Image, tuple[float, float] | None]],
    socket: dict[str, Any],
) -> dict[str, Any]:
    """Judge labeled frames. The head and the canvas must not translate."""
    frames = {name: samples[name][0] for name in GAZES if name in samples}
    centroids = {name: samples[name][1] for name in GAZES if name in samples}
    if set(frames) != set(GAZES):
        return _unavailable("the iris or socket bytes are missing")
    if any(frame.tobytes() == frames["center"].tobytes() for name, frame in frames.items() if name != "center"):
        return _unavailable("directions repeated the same pixels", _blank_probes())
    if not _outside_stable(frames, socket):
        return _unavailable("pixels outside the eyes moved", _blank_probes())
    center = centroids.get("center")
    probes: dict[str, Any] = {"center": {"pass": center is not None, "delta": 0.0}}
    checks = {"left": ("x", -1, 0.6), "right": ("x", 1, 0.6), "up": ("y", -1, 0.35), "down": ("y", 1, 0.35)}
    ready = center is not None
    for name, (axis, sign, need) in checks.items():
        point = centroids.get(name)
        delta = None
        if point is not None and center is not None:
            delta = point[0] - center[0] if axis == "x" else point[1] - center[1]
        passed = delta is not None and sign * delta > need
        probes[name] = {"pass": passed, "delta": None if delta is None else round(float(delta), 2)}
        ready = ready and passed
    if not ready:
        reason = _direction_reason(probes)
        return _unavailable(reason, probes)
    return {"gaze": "separable", "reason": None, "probes": probes}


def fit_travel(
    socket: Image.Image,
    iris: Image.Image,
    rest_x: float,
    rest_y: float,
    desired: tuple[int, int] = _PROOF_TRAVEL,
) -> tuple[int, int] | None:
    """Largest integer shift, from ``desired`` downward, that keeps 75% of the iris inside."""
    def fit(axis: int) -> int | None:
        travel = max(1, int(round(desired[axis])))
        while travel >= 1:
            dx, dy = (travel, 0) if axis == 0 else (0, travel)
            if _coverage(socket, iris, rest_x, rest_y, dx, dy) >= 0.75 and _coverage(socket, iris, rest_x, rest_y, -dx, -dy) >= 0.75:
                return travel
            travel -= 1
        return None

    x_travel, y_travel = fit(0), fit(1)
    if x_travel is None or y_travel is None:
        return None
    return x_travel, y_travel


def assess_skin(skin: Path) -> dict[str, Any]:
    """Probe views that were actually drawn. A missing file is not a fake view."""
    views: dict[str, Any] = {}
    for name in ("front", "left", "right"):
        path = skin / "views" / name / "view.json"
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            views[name] = _unavailable("the iris or socket bytes are missing")
            continue
        layers = data.get("layers") if isinstance(data, dict) else None
        if not isinstance(layers, list) or not layers:
            continue
        for layer in layers:
            if isinstance(layer, dict) and layer.get("url"):
                url = Path(str(layer["url"]))
                if not url.is_absolute():
                    layer["url"] = str((path.parent / url).resolve())
        views[name] = probe_view(data)
    ready = bool(views) and all(item.get("gaze") in {"separable", "recovered"} for item in views.values())
    report: dict[str, Any] = {"schema": SCHEMA, "version": 1, "ready": ready, "views": views}
    if not views:
        report["reason"] = "no supplied face view"
    return report


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


def _load(layers: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for layer in layers:
        name = str(layer.get("name"))
        if name not in {"eye_white", "eyes", "iris", "eye_socket", "head"} and not name:
            continue
        image = Image.open(layer["url"]).convert("RGBA")
        box = (max(1, int(layer["w"])), max(1, int(layer["h"])))
        if image.size != box:
            image = image.resize(box, Image.Resampling.NEAREST)
        found[name] = {"image": image, "x": int(layer["x"]), "y": int(layer["y"]), "z": int(layer.get("z") or 0)}
    return found


def _base_frame(view: dict[str, Any], loaded: dict[str, dict[str, Any]]) -> Image.Image:
    width, height = int(view["canvas"]["w"]), int(view["canvas"]["h"])
    stage = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    skip = {"iris", "eye_socket", "eyes_half", "eyes_closed"}
    ordered = []
    for layer in view["layers"]:
        name = str(layer.get("name"))
        if name in skip or name not in loaded:
            continue
        ordered.append(loaded[name] | {"name": name})
    for part in sorted(ordered, key=lambda item: item["z"]):
        stage.alpha_composite(part["image"], (part["x"], part["y"]))
    return stage


def _compose(plate: dict[str, Any], iris: dict[str, Any], socket: dict[str, Any], offset: tuple[int, int]) -> Image.Image:
    """Socket-sized eye. Rest iris ink is cleared, then the disc is drawn at ``offset``."""
    sock = np.array(socket["image"].convert("RGBA"))
    plate_arr = np.asarray(plate["image"].convert("RGBA"))
    iris_arr = np.asarray(iris["image"].convert("RGBA"))
    height, width = sock.shape[:2]
    grid_y, grid_x = np.indices((height, width))
    open_mask = sock[:, :, 3] > _ALPHA
    sock[:, :, 3] = np.where(open_mask, sock[:, :, 3], 0)
    px = grid_x - (plate["x"] - socket["x"])
    py = grid_y - (plate["y"] - socket["y"])
    valid = open_mask & (px >= 0) & (py >= 0) & (px < plate_arr.shape[1]) & (py < plate_arr.shape[0])
    sock[valid, :3] = plate_arr[py[valid], px[valid], :3]
    _blit(sock, iris_arr, iris["x"] - socket["x"], iris["y"] - socket["y"], fill=_sclera_color(plate_arr))
    _blit(sock, iris_arr, iris["x"] - socket["x"] + offset[0], iris["y"] - socket["y"] + offset[1], fill=None)
    return Image.fromarray(sock, "RGBA")


def _blit(
    socket: np.ndarray,
    iris: np.ndarray,
    rest_x: float,
    rest_y: float,
    *,
    fill: tuple[int, int, int] | None,
) -> None:
    ys, xs = np.nonzero(iris[:, :, 3] >= _ALPHA)
    if xs.size == 0:
        return
    dest_x = np.rint(rest_x + xs).astype(np.int32)
    dest_y = np.rint(rest_y + ys).astype(np.int32)
    keep = (dest_x >= 0) & (dest_y >= 0) & (dest_x < socket.shape[1]) & (dest_y < socket.shape[0])
    dest_x, dest_y, xs, ys = dest_x[keep], dest_y[keep], xs[keep], ys[keep]
    open_dest = socket[dest_y, dest_x, 3] > _ALPHA
    dest_x, dest_y, xs, ys = dest_x[open_dest], dest_y[open_dest], xs[open_dest], ys[open_dest]
    if fill is not None:
        socket[dest_y, dest_x, 0] = fill[0]
        socket[dest_y, dest_x, 1] = fill[1]
        socket[dest_y, dest_x, 2] = fill[2]
        return
    alpha = iris[ys, xs, 3].astype(np.float32) / 255
    for channel in range(3):
        socket[dest_y, dest_x, channel] = np.rint(
            iris[ys, xs, channel] * alpha + socket[dest_y, dest_x, channel] * (1 - alpha)
        ).astype(np.uint8)


def _sclera_color(plate: np.ndarray) -> tuple[int, int, int]:
    alpha = plate[:, :, 3]
    lum = plate[:, :, 0].astype(np.int16) + plate[:, :, 1].astype(np.int16) + plate[:, :, 2].astype(np.int16)
    mask = (alpha > 180) & (lum > 360)
    if not np.any(mask):
        return (180, 180, 180)
    pixels = plate[mask][:, :3]
    return tuple(int(round(float(channel))) for channel in pixels.mean(axis=0))


def _paste_socket(base: Image.Image, eye: Image.Image, socket: dict[str, Any]) -> Image.Image:
    canvas = np.array(base.convert("RGBA"))
    eye_arr = np.asarray(eye.convert("RGBA"))
    sock = np.asarray(socket["image"].convert("RGBA"))
    y0, x0 = int(socket["y"]), int(socket["x"])
    height, width = eye_arr.shape[:2]
    y1, x1 = min(canvas.shape[0], y0 + height), min(canvas.shape[1], x0 + width)
    if y1 <= y0 or x1 <= x0:
        return Image.fromarray(canvas, "RGBA")
    src_y, src_x = slice(max(0, -y0), max(0, -y0) + (y1 - max(y0, 0))), slice(max(0, -x0), max(0, -x0) + (x1 - max(x0, 0)))
    dst_y, dst_x = slice(max(y0, 0), y1), slice(max(x0, 0), x1)
    mask = sock[src_y, src_x, 3] > _ALPHA
    canvas[dst_y, dst_x][mask] = eye_arr[src_y, src_x][mask]
    return Image.fromarray(canvas, "RGBA")


def _pupil(frame: Image.Image, socket: dict[str, Any]) -> tuple[float, float] | None:
    arr = np.asarray(frame.convert("RGBA"))
    x0, y0 = socket["x"], socket["y"]
    height, width = socket["image"].size[1], socket["image"].size[0]
    crop = arr[max(0, y0):y0 + height, max(0, x0):x0 + width]
    if crop.size == 0:
        return None
    lum = crop[:, :, 0].astype(np.int16) + crop[:, :, 1].astype(np.int16) + crop[:, :, 2].astype(np.int16)
    mask = (crop[:, :, 3] >= 128) & (lum <= 180)
    ys, xs = np.nonzero(mask)
    if xs.size == 0:
        return None
    return float(xs.mean() + max(0, x0)), float(ys.mean() + max(0, y0))


def _coverage(socket: Image.Image, iris: Image.Image, rest_x: float, rest_y: float, dx: int, dy: int) -> float:
    sock = np.asarray(socket.convert("RGBA"))
    arr = np.asarray(iris.convert("RGBA"))
    ys, xs = np.nonzero(arr[:, :, 3] >= _ALPHA)
    total = int(xs.size)
    if total == 0:
        return 0.0
    inside = 0
    for x, y in zip(xs.tolist(), ys.tolist(), strict=True):
        sx = int(round(rest_x + x + dx))
        sy = int(round(rest_y + y + dy))
        if 0 <= sx < sock.shape[1] and 0 <= sy < sock.shape[0] and sock[sy, sx, 3] > _ALPHA:
            inside += 1
    return inside / total


def _outside_stable(frames: dict[str, Image.Image], socket: dict[str, Any]) -> bool:
    center = np.asarray(frames["center"].convert("RGBA"))
    inside = _opening_mask(center.shape, socket)
    for name, frame in frames.items():
        if name == "center":
            continue
        other = np.asarray(frame.convert("RGBA"))
        if other.shape != center.shape or np.any(center[~inside] != other[~inside]):
            return False
    return True


def _opening_mask(shape: tuple[int, ...], socket: dict[str, Any]) -> np.ndarray:
    mask = np.zeros(shape[:2], dtype=bool)
    alpha = np.asarray(socket["image"].getchannel("A")) > _ALPHA
    y0, x0 = int(socket["y"]), int(socket["x"])
    y1, x1 = min(mask.shape[0], y0 + alpha.shape[0]), min(mask.shape[1], x0 + alpha.shape[1])
    if y1 <= max(y0, 0) or x1 <= max(x0, 0):
        return mask
    placed = np.zeros_like(mask)
    placed[max(y0, 0):y1, max(x0, 0):x1] = alpha[max(0, -y0):max(0, -y0) + (y1 - max(y0, 0)), max(0, -x0):max(0, -x0) + (x1 - max(x0, 0))]
    for dy in range(-2, 3):
        for dx in range(-2, 3):
            shifted = np.zeros_like(placed)
            src_y = slice(max(0, -dy), placed.shape[0] - max(0, dy))
            src_x = slice(max(0, -dx), placed.shape[1] - max(0, dx))
            dst_y = slice(max(0, dy), placed.shape[0] - max(0, -dy))
            dst_x = slice(max(0, dx), placed.shape[1] - max(0, -dx))
            shifted[dst_y, dst_x] = placed[src_y, src_x]
            mask |= shifted
    return mask


def _socket_box(socket: dict[str, Any]) -> dict[str, int]:
    return {"x": int(socket["x"]), "y": int(socket["y"]), "w": int(socket["image"].size[0]), "h": int(socket["image"].size[1])}


def _gaze(name: str) -> tuple[float, float]:
    return {"center": (0.0, 0.0), "left": (-1.0, 0.0), "right": (1.0, 0.0), "up": (0.0, -1.0), "down": (0.0, 1.0)}[name]


def _direction_reason(probes: dict[str, Any]) -> str:
    messages = {
        "left": "left gaze did not move the pupil left",
        "right": "right gaze did not move the pupil right",
        "up": "up gaze did not move the pupil up",
        "down": "down gaze did not move the pupil down",
    }
    for name, text in messages.items():
        if not probes.get(name, {}).get("pass"):
            return text
    return "each direction needs the same eyes"


def _blank_probes() -> dict[str, Any]:
    return {name: {"pass": False, "delta": None} for name in GAZES}


def _unavailable(reason: str, probes: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"gaze": "unavailable", "reason": reason, "probes": probes if probes is not None else {}}
