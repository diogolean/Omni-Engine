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
- Depth of field: the plates are pre-blurred at a few lens settings
  (:data:`DOF_FAR`, :data:`DOF_MID`); the frame blends the two nearest to
  the shot's zoom, so close-ups throw the set out of focus and a glide racks
  focus smoothly instead of popping.
- Grade: per-scenario lift/gain + S-curve LUT, a soft highlight bloom and a
  vignette centred on the subject of the shot.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter

W, H = 1280, 720
MARGIN = 1.3  # plate size / frame size
PW, PH = round(W * MARGIN), round(H * MARGIN)
OX, OY = (PW - W) / 2, (PH - H) / 2  # frame origin inside a plate
HORIZON = 0.6  # floor line (camera at chest height), fraction of H
PARALLAX = {"far": 0.5, "mid": 0.75, "stage": 1.0}


@dataclass(frozen=True)
class Light:
    """Per character: rim on the ``side`` it faces away from (-1 left, +1 right)."""

    side: int
    rim: tuple[float, float, float]
    strength: float = 0.75
    ambient: tuple[float, float, float] = (1.0, 1.0, 1.0)  # set colour wrapped onto the character


RIM = {
    "studio": [(1.0, 0.82, 0.6), (0.62, 0.86, 1.0)],
    "classroom": [(1.0, 0.9, 0.72), (1.0, 0.9, 0.72)],
    "arena": [(1.0, 0.38, 0.42), (0.42, 0.62, 1.0)],
    "space": [(0.55, 0.85, 1.0), (0.85, 0.6, 1.0)],
    "agora": [(1.0, 0.84, 0.58), (1.0, 0.9, 0.74)],  # low sun from the left, warm sky bounce on the right
}
# lift (shadows) and gain (highlights) per channel, S-curve amount
GRADE = {
    "studio": ((0.012, 0.03, 0.04), (1.04, 1.0, 0.94), 0.35),
    "classroom": ((0.03, 0.022, 0.012), (1.05, 1.0, 0.9), 0.3),
    "arena": ((0.02, 0.008, 0.04), (1.06, 0.97, 1.0), 0.45),
    "space": ((0.0, 0.02, 0.05), (0.96, 1.0, 1.08), 0.4),
    "agora": ((0.035, 0.03, 0.045), (1.03, 1.0, 0.93), 0.22),  # soft painted contrast, lifted violet shadows
}
# the set's bounce light, multiplied into the characters so they sit in the same air as the plates
AMBIENT = {
    "studio": (0.9, 0.93, 1.0),
    "classroom": (1.0, 0.92, 0.8),
    "arena": (0.98, 0.84, 0.95),
    "space": (0.82, 0.9, 1.04),
    "agora": (1.0, 0.95, 0.86),
}
ACCENT = {"studio": (245, 176, 66), "classroom": (240, 196, 110), "arena": (255, 84, 96), "space": (96, 214, 255),
          "agora": (224, 162, 74)}


def light_for(scenario: str, template: str, idx: int, place_x: float) -> Light:
    side = 1 if template == "solo" else (-1 if place_x < 0.5 else 1)
    rims = RIM.get(scenario, RIM["studio"])
    return Light(side=side, rim=rims[min(idx, len(rims) - 1)], ambient=AMBIENT.get(scenario, AMBIENT["studio"]))


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


def _floor(arr: np.ndarray, color: tuple[float, float, float], reflect: float, pattern: str = "seams",
           gloss: float = 1.0) -> None:
    """Perspective floor below the horizon: a mirrored, fading copy of the wall, then a texture
    in floor coordinates (``u`` across, ``v`` = depth) so seams converge on the vanishing point."""
    hy = int(_py(HORIZON))
    n = arr.shape[0] - hy
    wall = arr[max(0, hy - n):hy][::-1]
    fade = (np.linspace(1, 0, len(wall), dtype=np.float32) ** 1.6)[:, None, None]
    base = np.array(color, np.float32)[None, None, :]
    ramp = np.linspace(0.75, 1.15, n, dtype=np.float32)[:, None, None]  # nearer floor catches more key light
    floor = np.broadcast_to(base * ramp, (n, arr.shape[1], 3)).copy()
    floor[: len(wall)] += wall * reflect * gloss * fade
    x = np.arange(arr.shape[1], dtype=np.float32)[None, :]
    dz = np.arange(1, n + 1, dtype=np.float32)[:, None]  # rows below the horizon
    u = (x - _px(0.5)) / dz * 40.0  # floor units across (perspective)
    v = 4000.0 / dz  # floor units in depth
    aa = np.clip(dz / 60.0, 0.15, 1.0)  # seams fade out toward the horizon instead of aliasing
    if pattern == "planks":
        plank = np.floor(u / 22.0)
        tone = 0.86 + 0.14 * np.sin(plank * 12.9898) ** 2
        seam = (np.abs(((u / 22.0) % 1.0) - 0.5) > 0.47).astype(np.float32)
        joint = (np.abs(((v + plank * 37.0) / 160.0) % 1.0 - 0.5) > 0.493).astype(np.float32)
        floor *= (tone * (1 - 0.35 * aa * np.maximum(seam, joint)))[..., None]
    elif pattern == "stone":  # sun-warmed flagstones, each a slightly different tone
        cu = np.floor(u / 15.0)
        cv = np.floor(v / 4.5 + (cu % 2) * 0.5)  # rows staggered like laid stone
        tone = 0.86 + 0.14 * np.sin(cu * 12.9898 + cv * 78.233) ** 2
        su = (np.abs(((u / 15.0) % 1.0) - 0.5) > 0.48).astype(np.float32)
        sv = (np.abs(((v / 4.5 + (cu % 2) * 0.5) % 1.0) - 0.5) > 0.46).astype(np.float32)
        floor *= (tone * (1 - 0.2 * aa * np.maximum(su, sv)))[..., None]
    elif pattern == "tiles":
        su = (np.abs(((u / 30.0) % 1.0) - 0.5) > 0.475).astype(np.float32)
        sv = (np.abs(((v / 30.0) % 1.0) - 0.5) > 0.47).astype(np.float32)
        floor += (np.maximum(su, sv) * aa)[..., None] * np.array((0.03, 0.13, 0.18), np.float32)
    else:  # seams of large glossy panels
        su = (np.abs(((u / 60.0) % 1.0) - 0.5) > 0.488).astype(np.float32)
        sv = (np.abs(((v / 90.0) % 1.0) - 0.5) > 0.485).astype(np.float32)
        floor *= (1 - 0.3 * aa * np.maximum(su, sv))[..., None]
    arr[hy:] = floor
    arr[hy : hy + 2] += 0.03  # horizon edge catches the light


def _rect(x: np.ndarray, y: np.ndarray, x0: float, y0: float, x1: float, y1: float, soft: float = 1.5) -> np.ndarray:
    """Anti-aliased rectangle mask in frame fractions."""
    return (np.clip((x - _px(x0)) / soft + 0.5, 0, 1) * np.clip((_px(x1) - x) / soft + 0.5, 0, 1)
            * np.clip((y - _py(y0)) / soft + 0.5, 0, 1) * np.clip((_py(y1) - y) / soft + 0.5, 0, 1))


def _add(arr: np.ndarray, mask: np.ndarray, color: tuple[float, float, float]) -> None:
    arr += mask[..., None] * np.array(color, np.float32)


def _paint(arr: np.ndarray, mask: np.ndarray, color: tuple[float, float, float] | np.ndarray) -> None:
    arr[:] = arr * (1 - mask[..., None]) + mask[..., None] * np.asarray(color, np.float32)


def _far(scenario: str, places: list[float]) -> np.ndarray:
    """Back wall, set pieces and the floor. Practical lights sit where the rim lights come from:
    left of frame for the character on the left, right for the one on the right."""
    x, y = _grid(PW, PH)
    t = np.clip((y - _py(0.0)) / (_py(HORIZON) - _py(0.0)), 0, 1)[..., None]
    rims = RIM.get(scenario, RIM["studio"])
    if scenario == "agora":
        return _agora_far(x, y, places)
    if scenario == "classroom":
        arr = np.array((0.34, 0.29, 0.22), np.float32) * (1 - t) + np.array((0.27, 0.22, 0.16), np.float32) * t
        arr = arr * (0.94 + 0.06 * _noise(PW, PH, 9, 11)[..., None])
        _paint(arr, _rect(x, y, -0.2, HORIZON - 0.15, 1.2, HORIZON + 0.01), (0.2, 0.125, 0.07))  # wainscot
        _add(arr, _rect(x, y, -0.2, HORIZON - 0.155, 1.2, HORIZON - 0.145), (0.12, 0.09, 0.05))  # its rail
        panel = (np.abs(((x - _px(0)) / (0.11 * W)) % 1.0 - 0.5) > 0.47) * _rect(x, y, -0.2, HORIZON - 0.14, 1.2, HORIZON)
        arr -= panel[..., None] * 0.05
        x0, x1, y0, y1 = -0.1, 0.12, 0.06, 0.46  # sunlit window, left (the key/rim side of the teacher)
        _paint(arr, _rect(x, y, x0 - 0.012, y0 - 0.02, x1 + 0.012, y1 + 0.02), (0.18, 0.11, 0.06))
        sky = np.clip((y - _py(y0)) / (_py(y1) - _py(y0)), 0, 1)[..., None]
        glass = np.array((1.0, 0.93, 0.76), np.float32) * (1 - sky) + np.array((0.98, 0.8, 0.55), np.float32) * sky
        _paint(arr, _rect(x, y, x0, y0, x1, y1), glass)
        for mx in (0.01, 0.065):
            _paint(arr, _rect(x, y, mx - 0.004, y0, mx + 0.004, y1), (0.18, 0.11, 0.06))
        _paint(arr, _rect(x, y, x0, 0.255, x1, 0.263), (0.18, 0.11, 0.06))
        arr += _blob(x, y, _px(0.2), _py(0.3), 0.25 * W, 0.35 * H)[..., None] * np.array((0.2, 0.15, 0.08), np.float32)
        x0 = 0.86  # bookcase on the right
        _paint(arr, _rect(x, y, x0, 0.08, 1.15, HORIZON + 0.005), (0.16, 0.1, 0.055))
        books = Image.new("RGBA", (PW, PH), (0, 0, 0, 0))  # spines drawn with PIL (cheap), then laid in
        db = ImageDraw.Draw(books)
        rng = np.random.default_rng(5)
        palette = [(115, 31, 26), (38, 64, 102), (128, 102, 51), (51, 82, 46), (140, 128, 102), (90, 40, 70)]
        for shelf in range(4):
            sy1 = 0.1 + (shelf + 1) * 0.12
            bx = x0 + 0.012
            while bx < 1.13:
                bw = rng.uniform(0.008, 0.016)
                c = np.array(palette[int(rng.integers(len(palette)))]) * rng.uniform(0.6, 1.0)
                db.rectangle((_px(bx), _py(sy1 - rng.uniform(0.07, 0.095)), _px(bx + bw), _py(sy1 - 0.004)), fill=(*c.astype(int), 255))
                bx += bw + 0.002
            db.rectangle((_px(x0), _py(sy1 - 0.004), PW, _py(sy1 + 0.006)), fill=(61, 38, 20, 255))
        b = np.asarray(books, dtype=np.float32) / 255.0
        arr[:] = arr * (1 - b[..., 3:]) + b[..., :3] * b[..., 3:]
        for px in places:
            arr += _blob(x, y, _px(px), _py(0.4), 0.2 * W, 0.32 * H)[..., None] * np.array((0.1, 0.075, 0.045), np.float32)
        _floor(arr, (0.3, 0.19, 0.11), 0.1, "planks")
        arr += _blob(x, y, _px(0.3), _py(0.92), 0.4 * W, 0.12 * H)[..., None] * np.array((0.16, 0.11, 0.05), np.float32)
    elif scenario == "arena":
        arr = np.array((0.03, 0.022, 0.04), np.float32) * np.ones((PH, PW, 1), np.float32)
        arr += _blob(x, y, _px(0.0), _py(0.25), 0.4 * W, 0.5 * H)[..., None] * np.array((0.5, 0.05, 0.1), np.float32)
        arr += _blob(x, y, _px(1.0), _py(0.25), 0.4 * W, 0.5 * H)[..., None] * np.array((0.05, 0.13, 0.55), np.float32)
        band = _rect(x, y, -0.2, HORIZON - 0.075, 1.2, HORIZON - 0.04)  # LED ad board, red to blue
        mix = np.clip((x - _px(0.1)) / (0.8 * W), 0, 1)[..., None]
        led = np.array((0.95, 0.2, 0.3), np.float32) * (1 - mix) + np.array((0.25, 0.4, 1.0), np.float32) * mix
        stripes = (0.75 + 0.25 * (np.mod(x, 9) < 6))[..., None]
        arr = arr * (1 - band[..., None]) + band[..., None] * led * stripes * 0.75
        arr += _blob(x, y, _px(0.5), _py(HORIZON - 0.06), 0.9 * W, 0.05 * H)[..., None] * led * 0.12
        _paint(arr, _rect(x, y, 0.36, 0.1, 0.64, 0.36), (0.02, 0.02, 0.03))  # jumbotron
        scr = _rect(x, y, 0.37, 0.115, 0.63, 0.345)
        sm = np.clip((x - _px(0.37)) / (0.26 * W), 0, 1)[..., None]
        _paint(arr, scr, (np.array((0.5, 0.06, 0.12), np.float32) * (1 - sm) + np.array((0.06, 0.14, 0.55), np.float32) * sm)
               * (0.8 + 0.2 * _noise(PW, PH, 4, 21)[..., None]))
        _floor(arr, (0.035, 0.03, 0.045), 0.4)
        hy = _py(HORIZON)  # the ring: a lit disc with a glowing edge
        r = np.sqrt(((x - _px(0.5)) / (0.62 * W)) ** 2 + ((y - _py(0.95)) / (0.3 * H)) ** 2) * (y > hy)
        disc = np.clip((1 - r) * 25, 0, 1)
        edge = np.exp(-((r - 1) * 28) ** 2) * (y > hy)
        arr += disc[..., None] * 0.05 + edge[..., None] * led * 0.5
    elif scenario == "space":
        neb = _noise(PW, PH, 5, 7) * _noise(PW, PH, 11, 8)
        arr = np.array((0.008, 0.012, 0.04), np.float32) + neb[..., None] * np.array((0.42, 0.12, 0.5), np.float32)
        arr += (_noise(PW, PH, 4, 9) ** 3)[..., None] * np.array((0.0, 0.25, 0.32), np.float32)
        rng = np.random.default_rng(42)
        sx, sy, sb = rng.integers(0, PW, 900), rng.integers(0, PH, 900), rng.random(900) ** 3
        arr[sy, sx] += (0.25 + 0.75 * sb)[:, None]
        metal = (0.07, 0.08, 0.1)  # bridge window frame: mullions, header and sill
        for mx in (-0.05, 0.2, 0.5, 0.8, 1.05):
            _paint(arr, _rect(x, y, mx - 0.012, -0.2, mx + 0.012, HORIZON), metal)
            _add(arr, _rect(x, y, mx - 0.012, -0.2, mx - 0.008, HORIZON), (0.12, 0.2, 0.25))
        _paint(arr, _rect(x, y, -0.2, -0.2, 1.2, 0.035), metal)
        _paint(arr, _rect(x, y, -0.2, HORIZON - 0.045, 1.2, HORIZON + 0.005), (0.06, 0.07, 0.09))
        _add(arr, _rect(x, y, -0.2, HORIZON - 0.047, 1.2, HORIZON - 0.04), (0.1, 0.45, 0.6))
        arr += _blob(x, y, _px(0.0), _py(0.4), 0.18 * W, 0.4 * H)[..., None] * np.array(rims[0], np.float32) * 0.18
        arr += _blob(x, y, _px(1.0), _py(0.4), 0.18 * W, 0.4 * H)[..., None] * np.array(rims[1], np.float32) * 0.18
        _floor(arr, (0.05, 0.06, 0.08), 0.3, "tiles")
    else:  # studio: talk-show set
        arr = np.array((0.06, 0.07, 0.085), np.float32) * (1 - t) + np.array((0.1, 0.11, 0.125), np.float32) * t
        slat = (np.abs(((x - _px(0)) / (0.045 * W)) % 1.0 - 0.5) > 0.44).astype(np.float32)
        arr *= (1 - 0.25 * slat)[..., None]
        _paint(arr, _rect(x, y, 0.355, 0.1, 0.645, 0.43), (0.015, 0.015, 0.02))  # screen between the guests
        scr = _rect(x, y, 0.365, 0.115, 0.635, 0.415)
        sm = np.clip((x - _px(0.365)) / (0.27 * W), 0, 1)[..., None]
        glow = np.array(rims[0], np.float32) * (1 - sm) + np.array(rims[1], np.float32) * sm
        _paint(arr, scr, 0.03 + glow * (0.05 + 0.3 * _noise(PW, PH, 5, 31)[..., None] ** 2))
        arr += _blob(x, y, _px(0.5), _py(0.27), 0.22 * W, 0.25 * H)[..., None] * glow * 0.08
        for sx, col in ((0.045, rims[0]), (0.955, rims[1])):  # the practicals behind the rim lights
            arr += _blob(x, y, _px(sx), _py(0.3), 0.07 * W, 0.45 * H)[..., None] * np.array(col, np.float32) * 0.35
            _paint(arr, _rect(x, y, sx - 0.006, 0.03, sx + 0.006, HORIZON - 0.01), np.array(col, np.float32) * 0.95)
        for px in places:
            arr += _blob(x, y, _px(px), _py(0.38), 0.17 * W, 0.3 * H)[..., None] * np.array((0.07, 0.065, 0.06), np.float32)
        _paint(arr, _rect(x, y, -0.2, HORIZON - 0.012, 1.2, HORIZON + 0.002), (0.03, 0.03, 0.035))  # skirting
        _floor(arr, (0.05, 0.052, 0.06), 0.45)
    return arr


def _cloud(arr: np.ndarray, x: np.ndarray, y: np.ndarray, cx: float, cy: float, size: float, seed: int) -> None:
    """A painted cumulus: overlapping puffs, warm lit top fading into a lavender belly (retro anime sky)."""
    rng = np.random.default_rng(seed)
    m = np.zeros(x.shape, np.float32)
    for _ in range(14):
        dx, dy = rng.uniform(-1.0, 1.0) * size, rng.uniform(-0.3, 0.12) * size
        r = rng.uniform(0.28, 0.55) * size * (1.0 - abs(dx) / (2.2 * size))
        m = np.maximum(m, np.clip((1 - np.sqrt((x - _px(cx) - dx * W) ** 2 + ((y - _py(cy) - dy * W) * 1.35) ** 2)
                                   / (r * W)) * 18, 0, 1))
    m *= np.clip((_py(cy) + 0.12 * size * W - y) / 6.0, 0, 1)  # flat cloud base
    k = np.clip((y - (_py(cy) - 0.45 * size * W)) / (0.6 * size * W), 0, 1)[..., None]
    col = np.array((1.0, 0.97, 0.9), np.float32) * (1 - k) + np.array((0.8, 0.74, 0.84), np.float32) * k
    _paint(arr, m, col)


def _agora_far(x: np.ndarray, y: np.ndarray, places: list[float]) -> np.ndarray:
    """Ancient set: a stone portico open on a golden-hour landscape (sky with painted clouds,
    hazy hills with a temple, cypresses), a parapet on the horizon and a flagstone floor.
    Classical personas (Sócrates, Platão...) debate here; the sun sits on the left like the key light."""
    t = np.clip((y - _py(-0.15)) / (_py(HORIZON) - _py(-0.15)), 0, 1)[..., None]
    arr = np.array((0.42, 0.62, 0.82), np.float32) * (1 - t) ** 1.3 + np.array((0.99, 0.86, 0.66), np.float32) * (1 - (1 - t) ** 1.3)
    arr += _blob(x, y, _px(0.05), _py(0.36), 0.35 * W, 0.3 * H)[..., None] * np.array((0.35, 0.22, 0.06), np.float32)
    for cx, cy, size, seed in ((0.3, 0.13, 0.1, 61), (0.62, 0.09, 0.13, 62), (0.97, 0.2, 0.08, 63), (0.06, 0.06, 0.07, 64)):
        _cloud(arr, x, y, cx, cy, size, seed)
    fx = (x - OX) / W  # frame-space x for the silhouettes
    ridge = 0.43 + 0.025 * np.sin(fx * 5.1 + 0.6) + 0.015 * np.sin(fx * 13.7 + 2.0)  # far hills, hazy
    _paint(arr, np.clip((y - _py(ridge)) / 1.5, 0, 1), np.array((0.62, 0.7, 0.72), np.float32))
    temple = (0.74, 0.4)  # a small temple on the far ridge, washed out by the haze
    _paint(arr, _rect(x, y, temple[0] - 0.04, temple[1] - 0.012, temple[0] + 0.04, temple[1] + 0.004), (0.86, 0.84, 0.8))
    for k in range(7):
        cx0 = temple[0] - 0.034 + k * 0.0113
        _paint(arr, _rect(x, y, cx0, temple[1] + 0.004, cx0 + 0.005, temple[1] + 0.03), (0.86, 0.84, 0.8))
    tri = (y < _py(temple[1] - 0.012)) & (y > _py(temple[1] - 0.012) - (0.045 * W - np.abs(x - _px(temple[0])) ) * 0.25)
    _paint(arr, tri.astype(np.float32), (0.88, 0.86, 0.82))
    ridge2 = 0.5 + 0.02 * np.sin(fx * 3.3 + 2.4) + 0.01 * np.sin(fx * 17.0)  # nearer hills with olive groves
    near = np.clip((y - _py(ridge2)) / 1.5, 0, 1)
    _paint(arr, near, np.array((0.44, 0.55, 0.4), np.float32))
    arr += (near * (_noise(PW, PH, 40, 65) > 0.62))[..., None] * np.array((-0.08, -0.06, -0.06), np.float32)  # tree clumps
    for cx, h in ((0.17, 0.3), (0.205, 0.24), (0.82, 0.34), (0.86, 0.26)):  # cypresses behind the parapet
        r = np.sqrt(((x - _px(cx)) / (0.016 * W)) ** 2 + ((y - _py(HORIZON - 0.05 - h / 2)) / (h / 2 * H)) ** 2)
        m = np.clip((1 - r) * 12, 0, 1)
        side = np.clip((x - _px(cx)) / (0.016 * W), -1, 1)[..., None]  # lit on the sun side
        _paint(arr, m, np.array((0.2, 0.3, 0.19), np.float32) * (1.15 - 0.3 * (side + 1) / 2))
    stone = np.array((0.86, 0.76, 0.6), np.float32)
    _paint(arr, _rect(x, y, -0.2, HORIZON - 0.06, 1.2, HORIZON + 0.003), stone * 0.82)  # parapet
    _add(arr, _rect(x, y, -0.2, HORIZON - 0.064, 1.2, HORIZON - 0.054), (0.1, 0.08, 0.04))  # its sunlit coping
    for cx in (-0.07, 0.07, 0.93, 1.07):  # portico columns framing the view, fluted, lit from the left
        x0, x1 = cx - 0.03, cx + 0.03
        u = np.clip((x - _px(x0)) / (_px(x1) - _px(x0)), 0, 1)
        shade = (1.08 - 0.42 * u ** 1.4) * (0.94 + 0.06 * np.cos(u * np.pi * 12) ** 2)
        _paint(arr, _rect(x, y, x0, -0.2, x1, HORIZON + 0.003), stone[None, None, :] * shade[..., None])
        _paint(arr, _rect(x, y, x0 - 0.012, 0.0, x1 + 0.012, 0.03), stone * 0.95)  # capital
        _paint(arr, _rect(x, y, x0 - 0.008, HORIZON - 0.02, x1 + 0.008, HORIZON + 0.003), stone * 0.78)  # base
    _paint(arr, _rect(x, y, -0.2, -0.2, 1.2, -0.0), stone * 0.72)  # architrave, mostly above frame
    _add(arr, _rect(x, y, -0.2, -0.02, 1.2, -0.0), (0.06, 0.05, 0.03))
    for px in places:  # warm pools where the speakers stand
        arr += _blob(x, y, _px(px), _py(0.55), 0.2 * W, 0.2 * H)[..., None] * np.array((0.06, 0.045, 0.02), np.float32)
    _floor(arr, (0.6, 0.49, 0.36), 0.08, "stone")
    arr += _blob(x, y, _px(0.15), _py(0.9), 0.45 * W, 0.14 * H)[..., None] * np.array((0.12, 0.08, 0.03), np.float32)
    for cx in (0.07, 0.93):  # column shadows raking across the floor, away from the sun
        sh = Image.new("L", (PW, PH), 0)
        ImageDraw.Draw(sh).polygon([(_px(cx - 0.03), _py(HORIZON)), (_px(cx + 0.03), _py(HORIZON)),
                                    (_px(cx + 0.36), _py(1.15)), (_px(cx + 0.2), _py(1.15))], fill=90)
        m = np.asarray(sh.filter(ImageFilter.GaussianBlur(14)), np.float32) / 255.0
        arr *= 1 - m[..., None] * np.array((0.36, 0.34, 0.29), np.float32)  # darker, a little cooler
    return arr


def _mid(scenario: str, template: str) -> Image.Image:
    """RGBA middle layer: haze and fixtures between the back wall and the characters."""
    img = Image.new("RGBA", (PW, PH), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    rng = np.random.default_rng({"studio": 1, "classroom": 2, "arena": 3, "space": 4, "agora": 5}.get(scenario, 1))
    if scenario == "agora":
        shaft = Image.new("L", (PW, PH), 0)  # late sun through the portico, raking to the right
        ds = ImageDraw.Draw(shaft)
        for x0, x1 in ((-0.04, 0.04), (0.1, 0.22)):
            ds.polygon([(_px(x0), _py(-0.05)), (_px(x1), _py(-0.05)), (_px(x1 + 0.55), _py(1.05)), (_px(x0 + 0.45), _py(1.05))], fill=26)
        shaft = shaft.filter(ImageFilter.GaussianBlur(44))
        img.alpha_composite(Image.merge("RGBA", (*Image.new("RGB", (PW, PH), (255, 226, 170)).split(), shaft)))
        for _ in range(70):  # pollen and dust drifting in the light
            cx, cy, r = rng.uniform(0, PW), rng.uniform(_py(0.05), _py(0.75)), rng.uniform(1.5, 4.0)
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(255, 240, 205, int(rng.uniform(40, 110))))
        leaf = Image.new("RGBA", (PW, PH), (0, 0, 0, 0))  # an olive branch hanging into the top right
        dl = ImageDraw.Draw(leaf)
        bx0, by0, bx1, by1 = _px(1.12), _py(-0.12), _px(0.8), _py(0.06)
        dl.line([(bx0, by0), ((bx0 + bx1) / 2, by0 + 40), (bx1, by1)], fill=(66, 52, 38, 255), width=7, joint="curve")
        for k in range(16):
            f = k / 15
            lx, ly = bx0 + (bx1 - bx0) * f, by0 + (by1 - by0) * f + 40 * math.sin(f * math.pi)
            side = 1 if k % 2 else -1
            ex, ey = lx + side * 34 + rng.uniform(-8, 8), ly + 44 + rng.uniform(-8, 12)
            g = int(rng.uniform(0, 26))
            dl.polygon([(lx, ly), ((lx + ex) / 2 + 11, (ly + ey) / 2 - 8), (ex, ey), ((lx + ex) / 2 - 11, (ly + ey) / 2 + 8)],
                       fill=(96 + g, 118 + g, 82 + g // 2, 255))
        img.alpha_composite(leaf.filter(ImageFilter.GaussianBlur(3.2)))  # nearer than the set: out of focus
        return img.filter(ImageFilter.GaussianBlur(1.2))
    if scenario == "classroom":
        if template != "lesson":  # the lesson draws its own board on the stage, with the text
            box = (_px(0.35), _py(0.1), _px(0.65), _py(0.44))
            d.rounded_rectangle(box, 10, fill=(70, 46, 28, 255))
            d.rounded_rectangle((box[0] + 12, box[1] + 12, box[2] - 12, box[3] - 12), 6, fill=(24, 44, 36, 255))
        shaft = Image.new("L", (PW, PH), 0)  # sunlight from the left window, onto the floor
        ds = ImageDraw.Draw(shaft)
        ds.polygon([(_px(-0.1), _py(0.06)), (_px(0.12), _py(0.06)), (_px(0.62), _py(1.0)), (_px(0.18), _py(1.0))], fill=30)
        shaft = shaft.filter(ImageFilter.GaussianBlur(40))
        img.alpha_composite(Image.merge("RGBA", (*Image.new("RGB", (PW, PH), (255, 236, 200)).split(), shaft)))
        return img.filter(ImageFilter.GaussianBlur(2.0))
    if scenario == "arena":
        cones = Image.new("L", (PW, PH), 0)
        dc = ImageDraw.Draw(cones)
        for fx in (0.22, 0.5, 0.78):
            dc.polygon([(_px(fx) - 14, _py(-0.02)), (_px(fx) + 14, _py(-0.02)), (_px(fx) + 200, _py(0.98)), (_px(fx) - 200, _py(0.98))], fill=30)
        cones = cones.filter(ImageFilter.GaussianBlur(24))
        img.alpha_composite(Image.merge("RGBA", (*Image.new("RGB", (PW, PH), (255, 236, 220)).split(), cones)))
        d.rectangle((0, _py(-0.07), PW, _py(-0.045)), fill=(20, 20, 26, 255))  # truss with the fixtures
        for fx in (0.22, 0.5, 0.78):
            d.rounded_rectangle((_px(fx) - 22, _py(-0.06), _px(fx) + 22, _py(-0.015)), 6, fill=(30, 30, 36, 255))
            d.ellipse((_px(fx) - 14, _py(-0.03), _px(fx) + 14, _py(-0.005)), fill=(255, 246, 230, 255))
        hy = _py(HORIZON - 0.075)  # crowd silhouettes above the ad board
        for row, (shade, dy) in enumerate(((8, 0), (5, -34))):
            for i in range(60):
                cx = (i + rng.uniform(-0.3, 0.3) + row * 0.5) * PW / 59
                r = rng.uniform(11, 17)
                top = hy + dy - rng.uniform(16, 34)
                d.ellipse((cx - r, top - r, cx + r, top + r), fill=(shade, shade - 2, shade + 3, 245))
                d.rounded_rectangle((cx - r * 1.6, top + r * 0.6, cx + r * 1.6, hy + dy + 6), 12, fill=(shade, shade - 2, shade + 3, 245))
        return img.filter(ImageFilter.GaussianBlur(1.6))
    if scenario == "space":
        cx, cy, r = _px(0.86), _py(0.2), 0.11 * W  # planet with a lit limb
        x, y = _grid(int(2 * r) + 4, int(2 * r) + 4)
        dx, dy = (x - r) / r, (y - r) / r
        inside = np.clip((1 - np.sqrt(dx**2 + dy**2)) * r, 0, 1)
        lit = np.clip(0.25 + 0.75 * (-dx * 0.6 - dy * 0.5), 0, 1) * (0.85 + 0.15 * _noise(*x.shape[::-1], 6, 3))
        col = np.stack([lit * 0.55, lit * 0.5, lit * 0.75, inside], -1)
        img.alpha_composite(Image.fromarray((col * 255).astype(np.uint8), "RGBA"), (int(cx - r), int(cy - r)))
        return img.filter(ImageFilter.GaussianBlur(1.2))
    haze = Image.new("L", (PW, PH), 0)  # studio: haze lit by the two practicals
    dh = ImageDraw.Draw(haze)
    for sx, k in ((0.045, 1), (0.955, -1)):
        dh.polygon([(_px(sx), _py(0.02)), (_px(sx + k * 0.02), _py(0.02)), (_px(sx + k * 0.3), _py(HORIZON)), (_px(sx + k * 0.05), _py(HORIZON))], fill=20)
    haze = haze.filter(ImageFilter.GaussianBlur(36))
    img.alpha_composite(Image.merge("RGBA", (*Image.new("RGB", (PW, PH), (255, 240, 225)).split(), haze)))
    for _ in range(14):  # a few out-of-focus practicals high on the wall
        cx, cy = rng.uniform(0, PW), rng.uniform(_py(-0.1), _py(0.08))
        r = rng.uniform(8, 20)
        col = (255, 196, 120) if cx < PW / 2 else (120, 210, 255)
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(*col, int(rng.uniform(25, 60))))
    return img.filter(ImageFilter.GaussianBlur(2.5))


def plates(scenario: str, template: str, places: list[float]) -> tuple[Image.Image, Image.Image]:
    """(far RGB plate with depth-of-field blur, mid RGBA plate)."""
    far = _rgb(_far(scenario, places)).filter(ImageFilter.GaussianBlur(2.2))
    return far, _mid(scenario, template)


# Extra blur (plate px) per lens setting, from the wide shot (index 0) to the tightest close-up.
# The far wall sits deeper than the mid layer (crowd, haze, fixtures), so it defocuses more.
DOF_FAR = (0.0, 2.6, 5.2, 8.0)
DOF_MID = (0.0, 1.8, 3.6, 5.6)
DOF_ZOOM = (1.06, 2.15)  # zoom of the sharpest and of the softest setting


def dof_stack(img: Image.Image, sigmas: tuple[float, ...]) -> list[Image.Image]:
    """``img`` blurred once per lens setting (the first setting is ``img`` itself when its sigma is 0)."""
    return [img if s <= 0 else img.filter(ImageFilter.GaussianBlur(s)) for s in sigmas]


def dof_level(zoom: float, levels: int) -> tuple[int, int, float]:
    """``(lower, upper, weight of upper)`` lens settings for a camera ``zoom``: continuous, so a push-in
    or a glide racks focus without steps."""
    u = min(1.0, max(0.0, (zoom - DOF_ZOOM[0]) / (DOF_ZOOM[1] - DOF_ZOOM[0])))
    u = u ** 0.85 * (levels - 1)  # over-the-shoulder singles already go noticeably soft
    lo = min(levels - 1, int(u))
    return lo, min(levels - 1, lo + 1), u - lo


BLOOM = {"studio": 0.22, "classroom": 0.2, "arena": 0.3, "space": 0.3, "agora": 0.26}


def bloom(rgb: Image.Image, strength: float, threshold: int = 178) -> Image.Image:
    """Soft halation around highlights (practicals, LED boards, rim light): the bright part of a
    quarter-size copy, blurred and screened back at ``strength``."""
    if strength <= 0:
        return rgb
    w, h = rgb.size
    small = rgb.resize((w // 4, h // 4), Image.BILINEAR)
    lut = [0 if v < threshold else round((v - threshold) * 255 / (255 - threshold)) for v in range(256)] * 3
    glow = small.point(lut).filter(ImageFilter.GaussianBlur(6)).resize((w, h), Image.BILINEAR)
    glow = glow.point([round(v * strength) for v in range(256)] * 3)
    return ImageChops.screen(rgb, glow)


# ---------------------------------------------------------------- set pieces on the stage

# lectern finish per scenario: (body rgb, top rgb, accent strip rgb or None)
LECTERN = {
    "studio": ((0.075, 0.08, 0.095), (0.2, 0.21, 0.23), "rim"),
    "classroom": ((0.36, 0.22, 0.12), (0.5, 0.33, 0.19), None),
    "arena": ((0.04, 0.035, 0.05), (0.14, 0.13, 0.16), "rim"),
    "space": ((0.16, 0.19, 0.23), (0.3, 0.35, 0.4), (0.3, 0.85, 1.0)),
    "agora": ((0.74, 0.65, 0.52), (0.88, 0.8, 0.66), None),  # a marble plinth
}


def lectern(scenario: str, size: tuple[int, int], light: Light) -> Image.Image:
    """Front-on lectern (RGBA, ``size`` px): top slab, slightly tapered body, lit from the key
    (inner) side with a rim on the outer edge in the same colour as the character's rim."""
    w, h = size
    body, top, strip = LECTERN.get(scenario, LECTERN["studio"])
    x = (np.arange(w, dtype=np.float32) + 0.5) / w
    y = (np.arange(h, dtype=np.float32) + 0.5) / h
    slab = 0.075
    inset = np.where(y < slab, 0.0, 0.035 + 0.05 * np.clip((y - slab) / (1 - slab), 0, 1))  # taper to the base
    edge = np.minimum(x[None, :] - inset[:, None], 1 - inset[:, None] - x[None, :]) * w
    a = np.clip(edge + 0.5, 0, 1)
    key = 1.0 + 0.35 * ((0.5 - x) * 2 * light.side)  # inner side is brighter
    shade = np.where(y < slab, 1.0, 0.95 - 0.45 * np.clip((y - slab) / (1 - slab), 0, 1))  # darker toward the floor
    col = np.where((y < slab)[:, None, None], np.array(top, np.float32), np.array(body, np.float32))
    rgb = col * key[None, :, None] * shade[:, None, None]
    if scenario == "agora":  # marble veins and fluting
        veins = _noise(w, h, 4, 23)
        rgb *= (0.95 + 0.07 * np.sin(x[None, :] * 40 + veins * 14) ** 8)[..., None]
        flute = (y >= slab)[:, None] * (np.cos(x[None, :] * np.pi * 18) ** 2)
        rgb *= (1 - 0.06 * flute)[..., None]
    if scenario == "classroom":  # wood grain and panel inlay
        grain = _noise(w, h, 3, 17)
        rgb *= (0.88 + 0.2 * np.sin(x[None, :] * 90 + grain * 9) * 0.5)[..., None]
        inlay = (np.abs(x - 0.5)[None, :] < 0.36) & (np.abs(y - 0.55)[:, None] < 0.3)
        rgb *= np.where(inlay, 0.85, 1.0)[..., None]
    lip = np.exp(-((y - slab) / 0.012) ** 2)[:, None, None]  # slab edge: highlight above, shadow below
    rgb = rgb + lip * (y < slab)[:, None, None] * 0.12 - lip * (y >= slab)[:, None, None] * 0.05
    rim = np.clip(1 - (edge - 0.5) / max(2.0, w * 0.012), 0, 1)
    outer = (x > 0.5) if light.side > 0 else (x < 0.5)
    rgb += (rim * outer[None, :])[..., None] * np.array(light.rim, np.float32) * 0.45
    if strip is not None:
        sc = np.array(light.rim if strip == "rim" else strip, np.float32)
        band = np.exp(-((y - 0.2) / 0.012) ** 2)[:, None] * (np.abs(x - 0.5) < 0.42)[None, :]
        glow = np.exp(-((y - 0.2) / 0.06) ** 2)[:, None] * (np.abs(x - 0.5) < 0.46)[None, :] * 0.25
        rgb += (band + glow)[..., None] * sc
    out = np.dstack([np.clip(rgb, 0, 1), a])
    return Image.fromarray((out * 255).astype(np.uint8))


def contact_shadow(size: tuple[int, int], strength: float = 0.75) -> Image.Image:
    """Soft dark ellipse (RGBA) for where an object meets the floor."""
    x, y = _grid(*size)
    r = np.sqrt(((x - size[0] / 2) / (size[0] / 2)) ** 2 + ((y - size[1] / 2) / (size[1] / 2)) ** 2)
    a = np.clip(1 - r, 0, 1) ** 1.3 * strength
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    img.putalpha(Image.fromarray((a * 255).astype(np.uint8)))
    return img


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
    key = 1.0 + 0.16 * ((0.5 - gx) * 2 * light.side)  # brighter on the key (inner) side
    fall = 1.0 - 0.45 * np.clip((gy - 0.62) / 0.3, 0, 1) ** 1.2  # occlusion toward the lectern
    rgb = rgb * key[None, :, None] * fall[:, None, None] * np.array(light.ambient, np.float32)
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
    return Image.fromarray((out * 255).astype(np.uint8))


def grade_lut(scenario: str) -> list[int]:
    lift, gain, s = GRADE.get(scenario, GRADE["studio"])
    x = np.linspace(0, 1, 256, dtype=np.float32)
    lut = []
    for c in range(3):
        y = np.clip(x * gain[c] + lift[c] * (1 - x), 0, 1)
        y = y + s * (y * y * (3 - 2 * y) - y)
        lut += list(np.clip(y * 255 + 0.5, 0, 255).astype(np.uint8).tolist())
    return lut


def vignette_field(strength: float = 0.5, size: tuple[int, int] = (W, H)) -> Image.Image:
    """A vignette twice the frame size; :func:`vignette_at` crops a frame-sized window from it so the
    falloff centres on the subject (the speaker's face in singles and close-ups)."""
    W, H = size  # noqa: N806
    x, y = _grid(2 * W, 2 * H)
    r = np.sqrt(((x - W) / (W / 2)) ** 2 + ((y - H) / (H / 2)) ** 2) / math.sqrt(2)
    v = 1 - strength * np.clip((r - 0.45) / 0.55, 0, 1) ** 1.6
    return Image.fromarray((np.clip(v, 0, 1) * 255).astype(np.uint8)).convert("RGB")


def vignette_at(field: Image.Image, centre: tuple[float, float]) -> Image.Image:
    """Frame-sized vignette from :func:`vignette_field` centred at ``centre`` (output px)."""
    W, H = field.width // 2, field.height // 2  # noqa: N806
    cx = min(max(centre[0], W * 0.25), W * 0.75)
    cy = min(max(centre[1], H * 0.3), H * 0.7)
    x0, y0 = round(W - cx), round(H - cy)  # the field's centre (W, H) lands on ``centre``
    return field.crop((x0, y0, x0 + W, y0 + H))


def vignette(strength: float = 0.5, size: tuple[int, int] = (W, H)) -> Image.Image:
    W, H = size  # noqa: N806
    x, y = _grid(W, H)
    r = np.sqrt(((x - W / 2) / (W / 2)) ** 2 + ((y - H / 2) / (H / 2)) ** 2) / math.sqrt(2)
    v = 1 - strength * np.clip((r - 0.45) / 0.55, 0, 1) ** 1.6
    return Image.fromarray((v * 255).astype(np.uint8)).convert("RGB")


def scrim(size: tuple[int, int] = (W, H), top: float = 0.68) -> Image.Image:
    """Bottom gradient that seats the captions."""
    W, H = size  # noqa: N806
    a = (np.clip((np.arange(H, dtype=np.float32) - H * top) / (H * (1 - top)), 0, 1) ** 1.5 * 150).astype(np.uint8)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    img.putalpha(Image.fromarray(np.repeat(a[:, None], W, axis=1)))
    return img


# ---------------------------------------------------------------- camera

@dataclass(frozen=True)
class Shot:
    cx: float  # frame-space centre
    cy: float
    zoom: float
    kind: str = ""  # shot grammar: wide, two, ots, cu, mcu, board


def handheld(t: float, amount: float = 1.0) -> tuple[float, float, float]:
    """Deterministic drift (dx, dy px) and roll (rad), a sum of slow sines."""
    dx = 3.2 * math.sin(0.53 * t) + 1.4 * math.sin(1.31 * t + 1.0)
    dy = 2.2 * math.sin(0.71 * t + 2.0) + 0.9 * math.sin(1.87 * t)
    roll = math.radians(0.14) * math.sin(0.41 * t + 0.5)
    return dx * amount, dy * amount, roll * amount


def clamp_shot(s: Shot, window_w: float = W) -> Shot:
    """Keep the camera window (``window_w`` frame px wide at zoom 1) inside the set."""
    z = max(1.04, s.zoom)
    hw, hh = window_w / (2 * z) + 8, H / (2 * z) + 8
    return Shot(min(max(s.cx, hw), W - hw), min(max(s.cy, hh), H - hh), z, s.kind)


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
