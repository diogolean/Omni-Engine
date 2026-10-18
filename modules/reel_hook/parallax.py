# -*- coding: utf-8 -*-
"""Optional 2.5D depth-parallax clip for the first scene of a reel.

Off by default. Ken Burns stays the hook unless ``REEL_HOOK_PARALLAX=1`` (or
``main.py --hook-parallax``). The renderer estimates depth on the CPU with
Depth Anything V2 Small, warps the still with an eased camera move and writes
an mp4. The reel compiler then burns the hook text, subtitles and logo on top,
as it does for any act video.

numpy, opencv, torch and transformers are imported only inside the renderer.
The Oracle image does not ship torch: with the flag on there, the renderer
logs the missing package and the hook falls back to Ken Burns.

Environment:
  REEL_HOOK_PARALLAX           1/true/on to enable. Default off.
  REEL_HOOK_PARALLAX_PRESET    push_orbit (default), dolly, orbit, dolly_haze.
  REEL_HOOK_PARALLAX_STRENGTH  Motion multiplier on the preset. Default 2.0,
                               twice the prototype, which read as too subtle.
  REEL_HOOK_PARALLAX_MIN_S     Shortest clip. Default 4.0.
  REEL_HOOK_PARALLAX_MAX_S     Longest clip. Default 5.0. A longer hook act
                               holds the last frame.
  REEL_HOOK_PARALLAX_CACHE_DIR Depth-map cache. Default: next to the clip.
  PARALLAX_DEPTH_MODEL         Hugging Face depth model id.
"""
from __future__ import annotations

import hashlib
import importlib.util
import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, replace
from pathlib import Path

logger = logging.getLogger(__name__)

_TRUE = {"1", "true", "yes", "on"}
_REQUIRED_MODULES = ("numpy", "cv2", "PIL", "torch", "transformers")
DEFAULT_PRESET = "push_orbit"
DEFAULT_STRENGTH = 2.0
_PIPE = None


@dataclass(frozen=True)
class Preset:
    dolly: float = 0.0       # extra magnification of the near plane at the end
    zoom: float = 0.0        # global zoom at the end
    orbit_x: float = 0.0     # lateral parallax amplitude (fraction of W)
    orbit_y: float = 0.0     # vertical parallax amplitude (fraction of H)
    focus: float = 0.55      # nearness that stays put (pivot)
    haze: float = 0.0        # atmospheric haze on the far plane
    haze_drift: float = 0.0  # fog drift (fraction of W over the clip)
    dilate_px: int = 6
    blur_px: float = 4.0
    margin: float = 1.10     # overscan canvas


# Prototype amplitudes. ``strength`` scales the motion terms.
PRESETS: dict[str, Preset] = {
    "dolly": Preset(dolly=0.075, zoom=0.035, orbit_x=0.004, focus=0.5),
    "orbit": Preset(orbit_x=0.022, orbit_y=0.004, zoom=0.03, focus=0.55),
    "dolly_haze": Preset(
        dolly=0.07, zoom=0.03, orbit_x=0.008, haze=0.16, haze_drift=0.05, focus=0.5,
    ),
    "push_orbit": Preset(dolly=0.05, zoom=0.03, orbit_x=0.016, orbit_y=0.003, focus=0.55),
}


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("HOOK parallax | %s=%r is not a number, using %s", name, raw, default)
        return default


def resolve_enabled(explicit: bool | None = None) -> bool:
    """An explicit argument wins; otherwise ``REEL_HOOK_PARALLAX``."""
    if explicit is not None:
        return bool(explicit)
    return os.environ.get("REEL_HOOK_PARALLAX", "").strip().lower() in _TRUE


def resolve_preset(name: str | None = None, strength: float | None = None) -> Preset:
    """Preset scaled by strength. Unknown names fall back to push_orbit."""
    key = (name or os.environ.get("REEL_HOOK_PARALLAX_PRESET", "") or DEFAULT_PRESET)
    key = key.strip().lower()
    base = PRESETS.get(key)
    if base is None:
        logger.warning("HOOK parallax | unknown preset %r, using %s", key, DEFAULT_PRESET)
        base = PRESETS[DEFAULT_PRESET]
    k = strength if strength is not None else _float_env(
        "REEL_HOOK_PARALLAX_STRENGTH", DEFAULT_STRENGTH,
    )
    k = max(0.0, min(float(k), 4.0))
    # Larger moves expose more background behind silhouettes; widen the
    # foreground dilation a little so the stretch lands on background.
    grow = max(1.0, k) ** 0.5
    return replace(
        base,
        dolly=base.dolly * k,
        zoom=base.zoom * k,
        orbit_x=base.orbit_x * k,
        orbit_y=base.orbit_y * k,
        dilate_px=int(round(base.dilate_px * grow)),
        margin=max(base.margin, 1.0 + base.zoom * k + 0.06),
    )


def clip_seconds(act_duration: float) -> float:
    """4–5 s by default, never longer than the act itself."""
    lo = _float_env("REEL_HOOK_PARALLAX_MIN_S", 4.0)
    hi = max(lo, _float_env("REEL_HOOK_PARALLAX_MAX_S", 5.0))
    want = min(max(float(act_duration), lo), hi)
    return max(0.5, min(want, float(act_duration))) if act_duration > 0 else lo


def missing_dependencies() -> list[str]:
    """Packages or binaries the renderer needs that are not installed."""
    missing = [m for m in _REQUIRED_MODULES if importlib.util.find_spec(m) is None]
    if _ffmpeg_exe() is None:
        missing.append("ffmpeg")
    return missing


def _ffmpeg_exe() -> str | None:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    if importlib.util.find_spec("imageio_ffmpeg") is not None:
        try:
            import imageio_ffmpeg  # noqa: PLC0415

            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:  # noqa: BLE001
            return None
    return None


def render_hook_clip(
    image_path: Path | str,
    out_dir: Path | str,
    *,
    act_duration: float,
    size: tuple[int, int] = (1080, 1920),
    fps: int = 30,
) -> Path | None:
    """Render the parallax hook. Returns None, never raises, on any failure."""
    image_path = Path(image_path)
    if not image_path.is_file():
        logger.warning("HOOK parallax | still missing (%s) — Ken Burns hook", image_path)
        return None
    missing = missing_dependencies()
    if missing:
        logger.warning(
            "HOOK parallax | missing %s — Ken Burns hook. "
            "Install torch (CPU) and transformers to use the parallax hook.",
            ", ".join(missing),
        )
        return None
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    preset = resolve_preset()
    seconds = clip_seconds(act_duration)
    out = out_dir / f"hook_parallax_{image_path.stem[:40]}.mp4"
    try:
        stats = _render(image_path, out, preset, seconds=seconds, fps=fps, size=size)
    except Exception as exc:  # noqa: BLE001
        logger.warning("HOOK parallax | render failed (%s) — Ken Burns hook", exc)
        return None
    if not out.is_file() or out.stat().st_size < 1024:
        logger.warning("HOOK parallax | empty output — Ken Burns hook")
        return None
    logger.info(
        "HOOK parallax | %s | preset=%s strength=%.2f %.1fs | depth=%.1fs total=%.1fs",
        out.name,
        os.environ.get("REEL_HOOK_PARALLAX_PRESET", DEFAULT_PRESET) or DEFAULT_PRESET,
        _float_env("REEL_HOOK_PARALLAX_STRENGTH", DEFAULT_STRENGTH),
        seconds, stats.get("secs_depth", 0.0), stats.get("secs_total", 0.0),
    )
    return out


def hook_video_override(
    image_paths: list,
    act_video_paths: list | None,
    *,
    act_duration: float,
    out_dir: Path | str,
    enabled: bool | None = None,
    size: tuple[int, int] = (1080, 1920),
    fps: int = 30,
) -> tuple[list, bool]:
    """Per-act video list with the parallax hook in slot 0.

    Returns ``(act_video_paths, hold_last_frame_on_act_0)``. With the flag off,
    or when act 0 already has a video, or on any failure, the list comes back
    unchanged and the hook stays Ken Burns.
    """
    videos = list(act_video_paths) if act_video_paths else []
    if not resolve_enabled(enabled) or not image_paths:
        return videos, False
    if videos and videos[0] and Path(videos[0]).is_file():
        return videos, False
    clip = render_hook_clip(
        image_paths[0], out_dir, act_duration=act_duration, size=size, fps=fps,
    )
    if clip is None:
        return videos, False
    if not videos:
        videos = [None] * len(image_paths)
    videos[0] = clip
    return videos, True


# ── renderer (heavy imports live below this line, inside functions) ──────────

def _estimate_depth(img_rgb, cache_dir: Path):
    """Relative nearness in [0, 1] (1 = closest), same HxW as the input."""
    import cv2  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415
    from PIL import Image  # noqa: PLC0415

    global _PIPE
    h, w = img_rgb.shape[:2]
    key = hashlib.md5(img_rgb.tobytes()).hexdigest()[:16]
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / f"depth_{key}.npy"
    if cached.is_file():
        return np.load(cached)
    if _PIPE is None:
        import torch  # noqa: PLC0415
        from transformers import pipeline  # noqa: PLC0415

        torch.set_num_threads(max(1, os.cpu_count() or 1))
        model = os.environ.get("PARALLAX_DEPTH_MODEL", "depth-anything/Depth-Anything-V2-Small-hf")
        _PIPE = pipeline("depth-estimation", model=model, device=-1)
    out = _PIPE(Image.fromarray(img_rgb))
    d = np.asarray(out["predicted_depth"], dtype=np.float32).squeeze()
    d = cv2.resize(d, (w, h), interpolation=cv2.INTER_CUBIC)
    lo, hi = np.percentile(d, 1), np.percentile(d, 99)
    d = np.clip((d - lo) / max(hi - lo, 1e-6), 0, 1)  # DA-V2 outputs inverse depth
    np.save(cached, d)
    return d


def _clean_depth(d, dilate_px: int, blur_px: float):
    import cv2  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    d = cv2.bilateralFilter(d.astype(np.float32), 9, 0.1, 7)
    if dilate_px > 0:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * dilate_px + 1,) * 2)
        d = cv2.dilate(d, k)
    if blur_px > 0:
        d = cv2.GaussianBlur(d, (0, 0), blur_px)
    return np.clip(d, 0, 1)


def _ease_in_out(t: float) -> float:
    return t * t * t * (t * (t * 6 - 15) + 10)


def _cover(img, w_out: int, h_out: int):
    import cv2  # noqa: PLC0415

    h, w = img.shape[:2]
    s = max(w_out / w, h_out / h)
    nw, nh = int(round(w * s)), int(round(h * s))
    interp = cv2.INTER_LANCZOS4 if s > 1 else cv2.INTER_AREA
    r = cv2.resize(img, (nw, nh), interpolation=interp)
    x0, y0 = (nw - w_out) // 2, (nh - h_out) // 2
    return r[y0:y0 + h_out, x0:x0 + w_out]


def _fog_field(w: int, h: int, seed: int = 7):
    import cv2  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    rng = np.random.default_rng(seed)
    f = np.zeros((h, w), np.float32)
    for cells, amp in ((6, 0.6), (14, 0.3), (30, 0.1)):
        n = rng.random((max(2, int(cells * h / w)), cells)).astype(np.float32)
        f += amp * cv2.resize(n, (w, h), interpolation=cv2.INTER_CUBIC)
    f = cv2.GaussianBlur(f, (0, 0), 25)
    return (f - f.min()) / (f.max() - f.min() + 1e-6)


def _render(in_path: Path, out_path: Path, p: Preset, *, seconds: float, fps: int,
            size: tuple[int, int], crf: int = 20) -> dict:
    import cv2  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415
    from PIL import Image  # noqa: PLC0415

    t0 = time.time()
    w_out, h_out = size
    cw = int(round(w_out * p.margin)) // 2 * 2
    ch = int(round(h_out * p.margin)) // 2 * 2
    src = np.asarray(Image.open(in_path).convert("RGB"))
    canvas = _cover(src, cw, ch)
    cache = Path(os.environ.get("REEL_HOOK_PARALLAX_CACHE_DIR") or out_path.parent)
    td = time.time()
    depth = _estimate_depth(_cover(src, cw // 2, ch // 2), cache)
    depth = cv2.resize(depth, (cw, ch), interpolation=cv2.INTER_CUBIC)
    t_depth = time.time() - td
    depth = _clean_depth(depth, int(p.dilate_px * cw / 1080), p.blur_px * cw / 1080)

    cx, cy = cw / 2.0, ch / 2.0
    u, v = np.meshgrid(
        np.arange(w_out, dtype=np.float32) - w_out / 2 + 0.5,
        np.arange(h_out, dtype=np.float32) - h_out / 2 + 0.5,
    )
    rel = (depth - p.focus).astype(np.float32)
    haze_col = fog = None
    if p.haze > 0:
        far = depth < np.percentile(depth, 20)
        base = np.median(canvas[far], axis=0) if far.any() else np.array([235, 235, 235])
        haze_col = np.clip(base * 0.6 + 255 * 0.4, 0, 255).astype(np.float32)
        fog = _fog_field(cw + int(w_out * max(p.haze_drift, 0.01)) + 8, ch)

    n = max(2, int(round(seconds * fps)))
    cmd = [
        _ffmpeg_exe() or "ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{w_out}x{h_out}", "-r", str(fps), "-i", "-", "-c:v", "libx264",
        "-preset", "medium", "-crf", str(crf), "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", str(out_path),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        for i in range(n):
            t = i / (n - 1)
            e = _ease_in_out(t)
            s_glob = 1.0 + p.zoom * e
            ox = p.orbit_x * (2 * e - 1) * w_out
            oy = p.orbit_y * (2 * e - 1) * h_out
            sx, sy = cx + u / s_glob, cy + v / s_glob
            for _ in range(2):  # fixed point: depth sampled at the source location
                r = cv2.remap(rel, sx, sy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
                s_loc = s_glob * (1.0 + p.dolly * e * r)
                sx = cx + u / s_loc - ox * r
                sy = cy + v / s_loc - oy * r
            frame = cv2.remap(canvas, sx, sy, cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT)
            if haze_col is not None:
                dd = cv2.remap(depth, sx, sy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
                shift = int(p.haze_drift * w_out * t)
                fz = cv2.remap(
                    np.ascontiguousarray(fog[:, shift:shift + cw]), sx, sy,
                    cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT,
                )
                a = (p.haze * (1 - dd) ** 1.6 * (0.55 + 0.45 * fz))[..., None]
                frame = (frame.astype(np.float32) * (1 - a) + haze_col * a)
                frame = frame.clip(0, 255).astype(np.uint8)
            proc.stdin.write(np.ascontiguousarray(frame).tobytes())
    finally:
        proc.stdin.close()
        proc.wait()
    if proc.returncode:
        raise RuntimeError(f"ffmpeg exited {proc.returncode}")
    return {
        "frames": n,
        "secs_total": round(time.time() - t0, 2),
        "secs_depth": round(t_depth, 2),
    }
