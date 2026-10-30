"""Cinematic look for the core compositor (:mod:`products.animind_core.scene`).

Everything here is precomputed once per job (plates, lit rig layers, LUT,
vignette) so the per-frame cost stays a few PIL C calls. Deterministic: the
only randomness is ``numpy.random.default_rng`` with fixed seeds.

- Plates: two parallax layers per scenario (``far`` blurred for depth of
  field, ``mid`` lightly blurred), larger than the frame so the camera can
  move; key-light pools sit behind the template's character places.
- Light: rim light on the outer edge of each rig layer, a key-side shading
  ramp and a bottom falloff that hides the bust cut; soft drop shadow per view.
- Camera: shots with slow push-in, handheld drift and roll, solved into PIL
  affine coefficients (output -> source) per parallax depth.
- Grade: per-scenario lift/gain + S-curve LUT and a vignette.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

W, H = 1280, 720
MARGIN = 1.3  # plate size / frame size
PW, PH = round(W * MARGIN), round(H * MARGIN)
OX, OY = (PW - W) / 2, (PH - H) / 2  # frame origin inside a plate
HORIZON = 0.8  # floor line, fraction of H
PARALLAX = {"far": 0.35, "mid": 0.65, "stage": 1.0}


@dataclass(frozen=True)
class Light:
    """Per character: rim on the ``side`` it faces away from (-1 left, +1 right)."""

    side: int
    rim: tuple[float, float, float]
    strength: float = 0.75


RIM = {
    "studio": [(1.0, 0.82, 0.6), (0.62, 0.86, 1.0)],
    "classroom": [(1.0, 0.9, 0.72), (1.0, 0.9, 0.72)],
    "arena": [(1.0, 0.38, 0.42), (0.42, 0.62, 1.0)],
    "space": [(0.55, 0.85, 1.0), (0.85, 0.6, 1.0)],
}
# lift (shadows) and gain (highlights) per channel, S-curve amount
GRADE = {
    "studio": ((0.012, 0.03, 0.04), (1.04, 1.0, 0.94), 0.35),
    "classroom": ((0.03, 0.022, 0.012), (1.05, 1.0, 0.9), 0.3),
    "arena": ((0.02, 0.008, 0.04), (1.06, 0.97, 1.0), 0.45),
    "space": ((0.0, 0.02, 0.05), (0.96, 1.0, 1.08), 0.4),
}
ACCENT = {"studio": (245, 176, 66), "classroom": (240, 196, 110), "arena": (255, 84, 96), "space": (96, 214, 255)}


def light_for(scenario: str, template: str, idx: int, place_x: float) -> Light:
    side = 1 if template == "solo" else (-1 if place_x < 0.5 else 1)
    rims = RIM.get(scenario, RIM["studio"])
    return Light(side=side, rim=rims[min(idx, len(rims) - 1)])


# ---------------------------------------------------------------- plates

def _grid(w: int, h: int) -> tuple[np.ndarray, np.ndarray]:
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    return x, y


def _blob(x: np.ndarray, y: np.ndarray, cx: float, cy: float, rx: float, ry: float) -> np.ndarray:
    return np.exp(-(((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2))


def _noise(w: int, h: int, cells: int, seed: int) -> np.ndarray:
    """Smooth value noise 0..1 (bicubic upscale of a seeded grid)."""
    g = np.random.default_rng(seed).random((cells, max(2, round(cells * w / h)))).astype(np.float32)
    im = Image.fromarray((g * 255).astype(np.uint8)).resize((w, h), Image.BICUBIC)
    return np.asarray(im, dtype=np.float32) / 255.0


def _rgb(arr: np.ndarray) -> Image.Image:
    return Image.fromarray((np.clip(arr, 0, 1) * 255).astype(np.uint8), "RGB")


def _px(fx: float) -> float:
    """Frame-space x fraction -> plate x."""
    return OX + fx * W


def _py(fy: float) -> float:
    return OY + fy * H


def _floor(arr: np.ndarray, color: tuple[float, float, float], reflect: float) -> None:
    """Floor below the horizon: darker, with a mirrored, fading copy of the wall."""
    hy = int(_py(HORIZON))
    n = arr.shape[0] - hy
    wall = arr[max(0, hy - n):hy][::-1]
    fade = np.linspace(1, 0, len(wall), dtype=np.float32)[:, None, None]
    base = np.array(color, np.float32)[None, None, :]
    ramp = np.linspace(1.0, 0.55, n, dtype=np.float32)[:, None, None]
    floor = np.broadcast_to(base * ramp, (n, arr.shape[1], 3)).copy()
    floor[: len(wall)] += wall * reflect * fade
    arr[hy:] = floor
    arr[hy : hy + 2] += 0.035  # horizon edge catches the light


def _far(scenario: str, places: list[float]) -> np.ndarray:
    x, y = _grid(PW, PH)
    t = (y / PH)[..., None]
    if scenario == "classroom":
        arr = np.array((0.3, 0.255, 0.2), np.float32) * (1 - t) + np.array((0.16, 0.13, 0.1), np.float32) * t
        arr = arr * (0.9 + 0.1 * _noise(PW, PH, 6, 11)[..., None])
        for wx in (0.04, 0.96):  # tall windows with cool daylight
            win = ((np.abs(x - _px(wx)) < 0.07 * W) & (y > _py(0.06)) & (y < _py(0.66))).astype(np.float32)
            arr += win[..., None] * np.array((0.55, 0.62, 0.7), np.float32)
        for px in places:
            arr += _blob(x, y, _px(px), _py(0.42), 0.22 * W, 0.4 * H)[..., None] * np.array((0.14, 0.11, 0.07), np.float32)
        _floor(arr, (0.2, 0.135, 0.085), 0.12)
        hy = int(_py(HORIZON))
        arr[hy:] *= (0.92 + 0.08 * (np.sin(x[hy:] / 9.0) > 0.96))[..., None]  # plank seams
    elif scenario == "arena":
        arr = np.array((0.035, 0.025, 0.045), np.float32) * np.ones((PH, PW, 1), np.float32)
        arr += _blob(x, y, _px(0.1), _py(0.35), 0.45 * W, 0.6 * H)[..., None] * np.array((0.45, 0.04, 0.09), np.float32)
        arr += _blob(x, y, _px(0.9), _py(0.35), 0.45 * W, 0.6 * H)[..., None] * np.array((0.04, 0.12, 0.48), np.float32)
        grid = ((np.mod(x, 46) < 2) | (np.mod(y, 46) < 2)).astype(np.float32) * (y < _py(HORIZON))
        arr += grid[..., None] * 0.025
        _floor(arr, (0.03, 0.025, 0.04), 0.35)
    elif scenario == "space":
        neb = _noise(PW, PH, 5, 7) * _noise(PW, PH, 11, 8)
        arr = np.array((0.008, 0.012, 0.04), np.float32) + neb[..., None] * np.array((0.42, 0.12, 0.5), np.float32)
        arr += (_noise(PW, PH, 4, 9) ** 3)[..., None] * np.array((0.0, 0.25, 0.32), np.float32)
        rng = np.random.default_rng(42)
        sx, sy, sb = rng.integers(0, PW, 900), rng.integers(0, PH, 900), rng.random(900) ** 3
        arr[sy, sx] += (0.25 + 0.75 * sb)[:, None]
        _floor(arr, (0.03, 0.04, 0.06), 0.25)
        hy = int(_py(HORIZON))
        arr[hy - 1 : hy + 3] += np.array((0.1, 0.45, 0.6), np.float32)
    else:  # studio
        arr = np.array((0.1, 0.115, 0.13), np.float32) * (1 - t) + np.array((0.04, 0.045, 0.05), np.float32) * t
        arr += _blob(x, y, _px(0.5), _py(0.38), 0.6 * W, 0.45 * H)[..., None] * np.array((0.05, 0.1, 0.12), np.float32)
        panels = 0.5 + 0.5 * np.cos((x - _px(0)) / (0.125 * W) * 2 * math.pi)
        arr *= (0.9 + 0.1 * panels)[..., None]
        for px in places:
            arr += _blob(x, y, _px(px), _py(0.45), 0.2 * W, 0.42 * H)[..., None] * np.array((0.2, 0.16, 0.12), np.float32)
        _floor(arr, (0.05, 0.05, 0.055), 0.3)
    return arr


def _mid(scenario: str, template: str) -> Image.Image:
    """RGBA middle layer: elements between the back wall and the characters."""
    img = Image.new("RGBA", (PW, PH), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    rng = np.random.default_rng({"studio": 1, "classroom": 2, "arena": 3, "space": 4}.get(scenario, 1))
    if scenario == "classroom":
        if template != "lesson":  # the lesson draws its own board on the stage, with the text
            box = (_px(0.33), _py(0.13), _px(0.67), _py(0.6))
            d.rounded_rectangle(box, 10, fill=(70, 46, 28, 255))
            d.rounded_rectangle((box[0] + 12, box[1] + 12, box[2] - 12, box[3] - 12), 6, fill=(24, 44, 36, 255))
        shaft = Image.new("L", (PW, PH), 0)  # daylight shafts from the windows
        ds = ImageDraw.Draw(shaft)
        for wx, k in ((0.04, 1), (0.96, -1)):
            ds.polygon([(_px(wx) - 60, _py(0.06)), (_px(wx) + 60, _py(0.06)), (_px(wx + k * 0.38), _py(1.0)), (_px(wx + k * 0.2), _py(1.0))], fill=26)
        shaft = shaft.filter(ImageFilter.GaussianBlur(30))
        img.alpha_composite(Image.merge("RGBA", (*Image.new("RGB", (PW, PH), (255, 244, 220)).split(), shaft)))
        return img.filter(ImageFilter.GaussianBlur(2.2))
    if scenario == "arena":
        cones = Image.new("L", (PW, PH), 0)
        dc = ImageDraw.Draw(cones)
        for fx in (0.2, 0.5, 0.8):
            dc.polygon([(_px(fx) - 18, _py(-0.05)), (_px(fx) + 18, _py(-0.05)), (_px(fx) + 230, _py(HORIZON)), (_px(fx) - 230, _py(HORIZON))], fill=34)
        cones = cones.filter(ImageFilter.GaussianBlur(26))
        img.alpha_composite(Image.merge("RGBA", (*Image.new("RGB", (PW, PH), (255, 236, 220)).split(), cones)))
        hy = _py(HORIZON)  # crowd silhouettes along the horizon
        for i in range(70):
            cx = (i + rng.uniform(-0.3, 0.3)) * PW / 69
            r = rng.uniform(15, 24)
            top = hy - rng.uniform(20, 46)
            d.ellipse((cx - r, top - r, cx + r, top + r), fill=(6, 5, 9, 235))
            d.rounded_rectangle((cx - r * 1.6, top + r * 0.6, cx + r * 1.6, hy + 30), 14, fill=(6, 5, 9, 235))
        return img.filter(ImageFilter.GaussianBlur(2.0))
    if scenario == "space":
        cx, cy, r = _px(0.86), _py(0.2), 0.11 * W  # planet with a lit limb
        x, y = _grid(int(2 * r) + 4, int(2 * r) + 4)
        dx, dy = (x - r) / r, (y - r) / r
        inside = np.clip((1 - np.sqrt(dx**2 + dy**2)) * r, 0, 1)
        lit = np.clip(0.25 + 0.75 * (-dx * 0.6 - dy * 0.5), 0, 1) * (0.85 + 0.15 * _noise(*x.shape[::-1], 6, 3))
        col = np.stack([lit * 0.55, lit * 0.5, lit * 0.75, inside], -1)
        img.alpha_composite(Image.fromarray((col * 255).astype(np.uint8), "RGBA"), (int(cx - r), int(cy - r)))
        for _ in range(30):
            sx, sy = rng.uniform(0, PW), rng.uniform(0, _py(HORIZON))
            s = rng.uniform(1.5, 3.2)
            d.ellipse((sx - s, sy - s, sx + s, sy + s), fill=(220, 235, 255, 230))
        return img.filter(ImageFilter.GaussianBlur(1.2))
    for _ in range(34):  # studio: out-of-focus practical lights (bokeh)
        cx, cy = rng.uniform(0, PW), rng.uniform(_py(0.02), _py(0.62))
        r = rng.uniform(10, 34)
        col = (255, 196, 120) if rng.random() < 0.6 else (120, 210, 255)
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(*col, int(rng.uniform(22, 70))))
    return img.filter(ImageFilter.GaussianBlur(3.0))


def plates(scenario: str, template: str, places: list[float]) -> tuple[Image.Image, Image.Image]:
    """(far RGB plate with depth-of-field blur, mid RGBA plate)."""
    far = _rgb(_far(scenario, places)).filter(ImageFilter.GaussianBlur(5))
    return far, _mid(scenario, template)


# ---------------------------------------------------------------- light

def lit_layer(img: Image.Image, origin: tuple[int, int], canvas: tuple[int, int], light: Light, rim: bool) -> Image.Image:
    """Bake rim light, key-side shading and a bottom falloff into one RGBA layer.

    ``origin`` is the layer's top-left in the (scaled) view canvas, ``canvas``
    the view size; the ramps use canvas coordinates so head and body match.
    """
    arr = np.asarray(img, dtype=np.float32) / 255.0
    rgb, a = arr[..., :3], arr[..., 3]
    h, w = a.shape
    gx = (np.arange(w, dtype=np.float32) + origin[0]) / max(1, canvas[0])
    gy = (np.arange(h, dtype=np.float32) + origin[1]) / max(1, canvas[1])
    key = 1.0 + 0.12 * ((0.5 - gx) * 2 * light.side)  # brighter on the key (inner) side
    fall = 1.0 - 0.55 * np.clip((gy - 0.72) / 0.28, 0, 1) ** 1.4
    rgb = rgb * key[None, :, None] * fall[:, None, None]
    if rim:
        dpx = max(2, round(min(w, h) * 0.014))
        nb = np.zeros_like(a)  # alpha of the neighbour toward the light (up and outward)
        sx = light.side * dpx
        src = a[: h - dpx, max(0, sx) : w + min(0, sx)]
        nb[dpx:, max(0, -sx) : w - max(0, sx)] = src
        edge = Image.fromarray((np.clip(a - nb, 0, 1) * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(dpx * 0.6))
        r = np.asarray(edge, dtype=np.float32)[..., None] / 255.0 * a[..., None] * light.strength
        rgb = 1 - (1 - np.clip(rgb, 0, 1)) * (1 - np.array(light.rim, np.float32) * r)
    out = np.dstack([np.clip(rgb, 0, 1), a])
    return Image.fromarray((out * 255).astype(np.uint8), "RGBA")


def drop_shadow(parts: list[tuple[Image.Image, tuple[int, int]]], size: tuple[int, int], radius: float) -> Image.Image:
    """Soft black shadow (RGBA) of the union of ``parts``, same size as the view."""
    pad = int(radius * 3)
    mask = Image.new("L", (size[0] + 2 * pad, size[1] + 2 * pad), 0)
    for img, (x, y) in parts:
        mask.paste(img.getchannel("A"), (x + pad, y + pad), img.getchannel("A"))
    mask = mask.filter(ImageFilter.GaussianBlur(radius)).point(lambda v: int(v * 0.55))
    shadow = Image.new("RGBA", mask.size, (0, 0, 0, 0))
    shadow.putalpha(mask)
    return shadow


# ---------------------------------------------------------------- grade

def grade_lut(scenario: str) -> list[int]:
    lift, gain, s = GRADE.get(scenario, GRADE["studio"])
    x = np.linspace(0, 1, 256, dtype=np.float32)
    lut = []
    for c in range(3):
        y = np.clip(x * gain[c] + lift[c] * (1 - x), 0, 1)
        y = y + s * (y * y * (3 - 2 * y) - y)
        lut += list(np.clip(y * 255 + 0.5, 0, 255).astype(np.uint8).tolist())
    return lut


def vignette(strength: float = 0.5) -> Image.Image:
    x, y = _grid(W, H)
    r = np.sqrt(((x - W / 2) / (W / 2)) ** 2 + ((y - H / 2) / (H / 2)) ** 2) / math.sqrt(2)
    v = 1 - strength * np.clip((r - 0.45) / 0.55, 0, 1) ** 1.6
    return Image.fromarray((v * 255).astype(np.uint8)).convert("RGB")


def scrim() -> Image.Image:
    """Bottom gradient that seats the captions."""
    a = (np.clip((np.arange(H, dtype=np.float32) - H * 0.68) / (H * 0.32), 0, 1) ** 1.5 * 150).astype(np.uint8)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    img.putalpha(Image.fromarray(np.repeat(a[:, None], W, axis=1)))
    return img


# ---------------------------------------------------------------- camera

@dataclass(frozen=True)
class Shot:
    cx: float  # frame-space centre
    cy: float
    zoom: float


def handheld(t: float, amount: float = 1.0) -> tuple[float, float, float]:
    """Deterministic drift (dx, dy px) and roll (rad), a sum of slow sines."""
    dx = 3.2 * math.sin(0.53 * t) + 1.4 * math.sin(1.31 * t + 1.0)
    dy = 2.2 * math.sin(0.71 * t + 2.0) + 0.9 * math.sin(1.87 * t)
    roll = math.radians(0.14) * math.sin(0.41 * t + 0.5)
    return dx * amount, dy * amount, roll * amount


def clamp_shot(s: Shot) -> Shot:
    z = max(1.04, s.zoom)
    hw, hh = W / (2 * z) + 8, H / (2 * z) + 8
    return Shot(min(max(s.cx, hw), W - hw), min(max(s.cy, hh), H - hh), z)


def affine(s: Shot, depth: str, drift: tuple[float, float, float], scale: float = 1.0) -> tuple[float, ...]:
    """PIL AFFINE data (output pixel -> source pixel) for a parallax depth.

    ``scale`` is the source's pixels per frame pixel (stage supersampling).
    Plates are bigger than the frame, so depths < 1 see a damped camera.
    """
    p = PARALLAX[depth]
    z = 1 + (s.zoom - 1) * p
    if depth == "stage":
        cx, cy, ox, oy = s.cx, s.cy, 0.0, 0.0
    else:
        cx, cy, ox, oy = W / 2 + (s.cx - W / 2) * p, H / 2 + (s.cy - H / 2) * p, OX, OY
    dx, dy, roll = (v * p for v in drift)
    co, si = math.cos(roll) / z, math.sin(roll) / z
    a, b, d, e = co, si, -si, co
    c = cx + ox - a * (W / 2 + dx) - b * (H / 2 + dy)
    f = cy + oy - d * (W / 2 + dx) - e * (H / 2 + dy)
    return tuple(v * scale for v in (a, b, c, d, e, f))


def ease(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)
