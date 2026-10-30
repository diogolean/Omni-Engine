"""Eye motion: blinks at random intervals and a gaze target per character.

Pure, deterministic functions of time and a seed, mirrored in the app
(``lib/eyes.ts``) so the browser preview and the MP4 move the same way.

Gaze is a screen-space vector ``(gx, gy)`` in ``[-1, 1]``: ``gx = +1`` is the
iris at the right edge of its socket, ``gy = +1`` at the bottom. Behaviour:

- default: look at the other speaker (``toward`` = +1 right, -1 left);
- sometimes at the camera, more often in the front view and on close-ups;
  in a 3/4 view "at camera" turns the iris toward the viewer side;
- short wanders (up, down, aside) with small saccadic jitter.
Blinks: every 2.2-5.6 s, half -> closed -> half (about 0.2 s).
"""
from __future__ import annotations

import math

SEG_MIN, SEG_MAX = 0.8, 2.4  # seconds per gaze segment
BLINK_MIN, BLINK_MAX = 2.2, 5.6
SACCADE_S = 0.07


def rand(seed: int, n: int) -> float:
    """Hash -> [0, 1). Same integer math as ``lib/eyes.ts``."""
    x = (seed * 374761393 + n * 668265263) & 0xFFFFFFFF
    x = ((x ^ (x >> 13)) * 1274126177) & 0xFFFFFFFF
    x ^= x >> 16
    return (x & 0xFFFFFF) / 0x1000000


def blink(t: float, seed: int) -> str:
    """``open``, ``half`` or ``closed`` at time ``t``."""
    start, n = 0.6 + rand(seed, 9999) * 1.5, 0
    while start <= t:
        u = t - start
        if u < 0.05 or 0.16 <= u < 0.22:
            return "half"
        if u < 0.16:
            return "closed"
        n += 1
        start += BLINK_MIN + rand(seed, n) * (BLINK_MAX - BLINK_MIN)
    return "open"


def _segment(t: float, seed: int) -> tuple[int, float]:
    start, n = 0.0, 0
    while True:
        dur = SEG_MIN + rand(seed + 17, n) * (SEG_MAX - SEG_MIN)
        if t < start + dur:
            return n, start
        start += dur
        n += 1


def camera_target(view: str, facing: int) -> tuple[float, float]:
    """Gaze that reads as "into the lens": centred in front, toward the viewer in 3/4."""
    return (0.0, 0.0) if view == "front" else (-0.45 * facing, 0.0)


def target(n: int, seed: int, *, toward: int, view: str, facing: int, camera_bias: float) -> tuple[float, float]:
    r = rand(seed + 31, n)
    cam = min(0.85, (0.45 if view == "front" else 0.12) + camera_bias)
    if r < cam:
        return camera_target(view, facing)
    if r < cam + (1 - cam) * 0.22:  # wander
        a = rand(seed + 47, n) * math.tau
        return (0.6 * math.cos(a), 0.5 * math.sin(a))
    return (0.85 * toward, 0.05 + 0.15 * (rand(seed + 53, n) - 0.5))


def gaze(t: float, seed: int, *, toward: int, view: str = "left", facing: int = -1,
         camera_bias: float = 0.0) -> tuple[float, float]:
    """Gaze vector at ``t``. ``facing`` is the head's screen direction (-1 left view, +1 right view, 0 front)."""
    n, start = _segment(t, seed)
    kw = dict(toward=toward, view=view, facing=facing, camera_bias=camera_bias)
    cur = target(n, seed, **kw)
    if n > 0 and t - start < SACCADE_S:
        prev = target(n - 1, seed, **kw)
        a = (t - start) / SACCADE_S
        a = a * a * (3 - 2 * a)
        cur = (prev[0] + (cur[0] - prev[0]) * a, prev[1] + (cur[1] - prev[1]) * a)
    jx = 0.05 * math.sin(t * 7.1 + seed) * (rand(seed + 71, n) > 0.5)
    return (max(-1.0, min(1.0, cur[0] + jx)), max(-1.0, min(1.0, cur[1])))
