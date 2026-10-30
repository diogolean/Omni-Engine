"""Animation styles: one id drives the Gemini character prompt and the scene look.

``prompt`` replaces the drawing-style sentence of :data:`rig.STYLE_PROMPT`; the
look part (``grade``, ``tint``, ``grain``, ``lines``) is applied by :class:`scene.Scene`
on top of the scenario grade, so a style recolours the same sets instead of
needing new ones. ``cartoon`` is the default and is a no-op on the look.
"""
from __future__ import annotations

from typing import Any

import numpy as np
from PIL import Image, ImageFilter

STYLES: dict[str, dict[str, Any]] = {
    "cartoon": {"label": "Cartoon",
                "prompt": "Expressive 2D cartoon character design, clean flat cel shading, bold dark outlines",
                "grade": None, "tint": None, "grain": 0.0, "lines": 0.0},
    "anime": {"label": "Anime",
              "prompt": "Japanese anime character design, crisp thin lineart, large expressive eyes, soft two-tone cel shading, vivid colors",
              "grade": ((0.02, 0.02, 0.04), (1.04, 1.03, 1.08), 0.25, 1.22), "tint": ((255, 214, 236), 0.10), "grain": 0.0, "lines": 0.0},
    "retro": {"label": "Retrô",
              "prompt": "1950s retro cartoon illustration, rubber-hose era, muted vintage palette, slightly worn print texture, thick ink outlines",
              "grade": ((0.07, 0.05, 0.02), (1.0, 0.94, 0.78), 0.3, 0.72), "tint": ((236, 206, 160), 0.28), "grain": 0.07, "lines": 0.0},
    "realista": {"label": "Realista",
                 "prompt": "Semi-realistic digital painting portrait, natural skin tones, soft realistic lighting and shading, subtle outlines",
                 "grade": ((0.0, 0.03, 0.05), (1.1, 1.0, 0.86), 0.45, 0.8), "tint": ((222, 196, 160), 0.2), "grain": 0.035, "lines": 0.0,
                 # filmic: shallow depth of field on the sets, dark lens vignette
                 "blur": 6.0, "vignette": ((0, 0, 0), 0.8)},
    "cyberpunk": {"label": "Cyberpunk",
                  "prompt": "Cyberpunk neon character design, sharp outlines, magenta and cyan neon rim light, futuristic details on clothing",
                  "grade": ((0.03, 0.0, 0.07), (1.02, 0.9, 1.15), 0.5, 1.25), "tint": ((120, 60, 220), 0.30), "grain": 0.03, "lines": 0.10},
    "aquarela": {"label": "Aquarela",
                 "prompt": "Watercolor storybook illustration, soft washes of color, delicate pencil outlines, paper texture",
                 "grade": ((0.16, 0.15, 0.13), (1.0, 0.99, 0.95), -0.1, 0.72), "tint": ((250, 236, 210), 0.35), "grain": 0.0, "lines": 0.0,
                 # washes: soft posterised sets, paper fibre blotches, pale paper edges
                 "blur": 2.5, "wash": 6, "paper": 0.16, "vignette": ((246, 238, 222), 0.6)},
}
DEFAULT = "cartoon"


def pick(style: Any) -> str:
    return style if isinstance(style, str) and style in STYLES else DEFAULT


def catalog() -> list[dict[str, str]]:
    return [{"id": k, "label": v["label"]} for k, v in STYLES.items()]


def prompt_style(style: str) -> str:
    return STYLES[pick(style)]["prompt"]


def lut(base: list[int], style: str) -> list[int]:
    """Compose the scenario LUT with the style's lift/gain/contrast (saturation is done per frame)."""
    g = STYLES[pick(style)]["grade"]
    if not g:
        return base
    lift, gain, s = g[0], g[1], g[2]
    x = np.linspace(0, 1, 256, dtype=np.float32)
    out = []
    for c in range(3):
        y = np.clip(x * gain[c] + lift[c] * (1 - x), 0, 1)
        y = y + s * (y * y * (3 - 2 * y) - y)
        mine = np.clip(y * 255 + 0.5, 0, 255).astype(np.uint8)
        out += [int(mine[v]) for v in base[c * 256:(c + 1) * 256]]
    return out


def saturation(style: str) -> float:
    g = STYLES[pick(style)]["grade"]
    return g[3] if g else 1.0


def tint_plate(img: Image.Image, style: str) -> Image.Image:
    """Recolour a background plate toward the style palette (keeps alpha)."""
    st = STYLES[pick(style)]
    t = st["tint"]
    if not t:
        return img
    color, amt = t
    rgba = img.convert("RGBA")
    if st.get("blur"):
        rgba = rgba.filter(ImageFilter.GaussianBlur(st["blur"]))
    if st.get("wash"):  # flat watercolour washes: quantise, then soften the steps
        rgb = rgba.convert("RGB").quantize(colors=st["wash"] * 4, method=Image.Quantize.MEDIANCUT).convert("RGB")
        rgb = rgb.filter(ImageFilter.ModeFilter(5)).filter(ImageFilter.GaussianBlur(1.5))
        rgb.putalpha(rgba.getchannel("A"))
        rgba = rgb
    a = np.asarray(rgba, dtype=np.float32)
    lum = a[..., :3].mean(axis=2, keepdims=True) / 255.0
    tinted = lum * np.array(color, np.float32)
    a[..., :3] = a[..., :3] * (1 - amt) + tinted * amt
    return Image.fromarray(a.clip(0, 255).astype(np.uint8), "RGBA")


def texture(style: str, size: tuple[int, int], seed: int = 7) -> Image.Image | None:
    """Static grain + scanlines overlay (RGBA, blended over the frame), or None."""
    st = STYLES[pick(style)]
    if not st["grain"] and not st["lines"] and not st.get("paper") and not st.get("vignette"):
        return None
    w, h = size
    rng = np.random.default_rng(seed)
    n = rng.normal(0, 1, (h, w)).astype(np.float32)
    alpha = np.abs(n) * st["grain"] * 255
    val = np.where(n > 0, 255, 0).astype(np.float32)
    rgb = np.dstack([val, val, val])
    if st.get("paper"):  # low-frequency fibre blotches, darker than the paper
        small = Image.fromarray((rng.random((h // 24 + 1, w // 24 + 1)) * 255).astype(np.uint8))
        blot = np.asarray(small.resize((w, h), Image.BICUBIC), np.float32) / 255.0
        fine = np.asarray(Image.fromarray((rng.random((h // 3 + 1, w // 3 + 1)) * 255).astype(np.uint8)).resize((w, h), Image.BICUBIC), np.float32) / 255.0
        pa = (np.abs(blot - 0.5) * 1.4 + fine * 0.35) * st["paper"] * 255
        rgb = rgb * (alpha / np.maximum(alpha + pa, 1))[..., None] + np.array([150, 128, 96], np.float32) * (pa / np.maximum(alpha + pa, 1))[..., None]
        alpha = alpha + pa
    if st.get("vignette"):
        color, amt = st["vignette"]
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        r = np.sqrt(((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2) / np.sqrt(2)
        va = np.clip((r - 0.45) / 0.55, 0, 1) ** 1.6 * amt * 255
        mix = va / np.maximum(alpha + va, 1)
        rgb = rgb * (1 - mix)[..., None] + np.array(color, np.float32) * mix[..., None]
        alpha = alpha + va
    val = None
    if st["lines"]:
        rows = (np.arange(h) % 4 < 1)[:, None].astype(np.float32)
        alpha = alpha + rows * st["lines"] * 255
        rgb = np.where((rows > 0)[..., None], 0, rgb)
    out = np.dstack([rgb.clip(0, 255), alpha.clip(0, 255)]).astype(np.uint8)
    return Image.fromarray(out, "RGBA")
