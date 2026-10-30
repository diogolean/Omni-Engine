"""Server compositor for contract v2 rigs, encoded by the Rigly renderer.

Draws the same scene as the app's browser preview (``lib/engine.ts``):
1280x720, scenario background, characters placed per template, blink,
breathing, the speaker's head lifting with the voice, the mouth sprite of
the current Rhubarb viseme, captions and the baked-in parody label. Frames
go to :class:`animation.parametric.renderer.AnimationRenderer` (ffmpeg pipe)
with the voice track muxed in.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import numpy as np
from PIL import Image, ImageDraw, ImageFont

W, H, FPS = 1280, 720, 24
_REPO = Path(__file__).resolve().parents[2]
_FONT_DIR = _REPO / "Fonts" / "Montserrat" / "static"
BG = {
    "studio": ((22, 22, 22), (11, 11, 11)),
    "classroom": ((20, 23, 26), (11, 12, 13)),
    "arena": ((26, 20, 20), (12, 10, 10)),
    "space": ((13, 14, 20), (5, 5, 7)),
}
# (x centre as a fraction of W, scale on top of "fit to 92% of the height")
PLACES = {"debate": [(0.27, 0.85), (0.73, 0.85)], "lesson": [(0.25, 0.9)], "solo": [(0.5, 0.95)]}


def _font(weight: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = _FONT_DIR / f"Montserrat-{weight}.ttf"
    return ImageFont.truetype(str(path), size) if path.is_file() else ImageFont.load_default()


@dataclass
class RigView:
    """One view, layers pre-scaled to the frame scale."""

    size: tuple[int, int]
    layers: list[tuple[str, Image.Image, tuple[int, int], int]] = field(default_factory=list)
    visemes: dict[str, tuple[Image.Image, tuple[int, int], int]] = field(default_factory=dict)


def load_rig_view(view: dict[str, Any], scale: float) -> RigView:
    """``view`` is a v2 CharacterView whose ``url`` fields are file paths."""
    cw, ch = view["canvas"]["w"], view["canvas"]["h"]

    def sized(layer: dict[str, Any]) -> tuple[Image.Image, tuple[int, int]]:
        with Image.open(layer["url"]) as im:
            img = im.convert("RGBA")
        w, h = max(1, round(layer["w"] * scale)), max(1, round(layer["h"] * scale))
        return img.resize((w, h), Image.LANCZOS), (round(layer["x"] * scale), round(layer["y"] * scale))

    rv = RigView(size=(round(cw * scale), round(ch * scale)))
    for layer in sorted(view["layers"], key=lambda l: l["z"]):
        img, xy = sized(layer)
        rv.layers.append((layer["name"], img, xy, layer["z"]))
    for v, layer in view["visemes"].items():
        img, xy = sized(layer)
        rv.visemes[v] = (img, xy, layer.get("z", 99))
    return rv


def _background(scenario: str) -> Image.Image:
    top, bottom = BG.get(scenario, BG["studio"])
    ramp = np.linspace(0.0, 1.0, H)[:, None, None]
    arr = (np.array(top)[None, None, :] * (1 - ramp) + np.array(bottom)[None, None, :] * ramp).repeat(W, axis=1)
    arr[int(H * 0.82):] = arr[int(H * 0.82):] * 0.965 + 255 * 0.035
    img = Image.fromarray(arr.astype(np.uint8)).convert("RGBA")
    if scenario == "space":
        d = ImageDraw.Draw(img)
        for i in range(90):
            x, y = (i * 137.5) % W, (i * 71.3) % (H * 0.8)
            d.rectangle((x, y, x + 1, y + 1), fill=(255, 255, 255, 120))
    return img


def _placeholder(frame: Image.Image, cx: int, lift: float) -> None:
    d = ImageDraw.Draw(frame)
    d.ellipse((cx - 160, int(H * 0.6), cx + 160, int(H * 1.1)), fill=(42, 42, 42, 255))
    d.ellipse((cx - 100, int(H * 0.25 - lift), cx + 100, int(H * 0.6 - lift)), fill=(51, 51, 51, 255))


def _wrap(d: ImageDraw.ImageDraw, text: str, font: Any, max_w: float) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        nxt = f"{cur} {word}".strip()
        if d.textlength(nxt, font=font) > max_w and cur:
            lines.append(cur)
            cur = word
        else:
            cur = nxt
    return lines + ([cur] if cur else [])


class Scene:
    """Iterates RGB frames for one render job."""

    def __init__(self, *, template: str, scenario: str, rigs: list[dict[str, RigView] | None], script: list[dict[str, Any]],
                 envelope: np.ndarray, env_fps: int, duration_s: float, disclaimer: str, fps: int = FPS) -> None:
        self.template = template if template in PLACES else "solo"
        self.rigs, self.script, self.env, self.env_fps = rigs, script, envelope, env_fps
        self.duration_s, self.disclaimer, self.fps = duration_s, disclaimer, fps
        self.bg = _background(scenario)
        self.caption_font = _font("Medium", 28)
        self.label_font = _font("SemiBold", 18)
        self.brand_font = _font("Medium", 16)
        self.board_font = _font("Medium", 28)

    def _line_at(self, t: float, grace: float = 0.0) -> dict[str, Any] | None:
        ms = t * 1000
        return next((l for l in self.script if l["startMs"] <= ms <= l["endMs"] + grace * 1000), None)

    def _viseme(self, line: dict[str, Any], t: float) -> str:
        cur = "X"
        for cue in line.get("visemes") or []:
            if cue["t"] > t:
                break
            cur = cue["v"]
        return cur

    def _view(self, idx: int, t: float) -> str:
        line = self._line_at(t, 0.35)
        if line and line["speaker"] == idx and line.get("view"):
            return line["view"]
        if self.template == "debate":
            return "right" if idx == 0 else "left"
        return "front"

    def _draw_char(self, frame: Image.Image, idx: int, t: float, amp: float, vis: str) -> None:
        cxf, _ = PLACES[self.template][min(idx, len(PLACES[self.template]) - 1)]
        cx = int(W * cxf)
        seed = 1.3 * idx
        breathe = math.sin(t * 1.6 + seed) * 2
        views = self.rigs[idx] if idx < len(self.rigs) else None
        if not views:
            _placeholder(frame, cx, amp * 4)
            return
        rv = views.get(self._view(idx, t)) or views.get("front")
        if rv is None:
            _placeholder(frame, cx, amp * 4)
            return
        x0, y0 = cx - rv.size[0] // 2, int(H * 0.98) - rv.size[1]
        blink = (t + seed) % 3.6 < 0.12
        mouth = rv.visemes.get(vis) or rv.visemes.get("X")
        parts = list(rv.layers) + ([("mouth", mouth[0], mouth[1], mouth[2])] if mouth else [])
        for name, img, (lx, ly), _z in sorted(parts, key=lambda p: p[3]):
            if (name == "eyes" and blink) or (name == "eyes_closed" and not blink):
                continue
            dy = breathe * 0.4 if name == "body" else breathe - amp * 4 - (amp * 6 if name == "brows" else 0)
            frame.alpha_composite(img, (x0 + lx, y0 + ly + round(dy)))

    def frame(self, t: float) -> Image.Image:
        frame = self.bg.copy()
        line = self._line_at(t)
        k = min(len(self.env) - 1, max(0, int(t * self.env_fps)))
        amp = float(self.env[k]) if len(self.env) else 0.0
        d = ImageDraw.Draw(frame)
        if self.template == "lesson":
            d.rounded_rectangle((W * 0.48, H * 0.14, W * 0.92, H * 0.62), 16, outline=(255, 255, 255, 31), width=2)
            cur = self._line_at(t, 0.35) or (self.script[0] if self.script else None)
            for i, s in enumerate(_wrap(d, cur["text"] if cur else "", self.board_font, W * 0.38)):
                d.text((W * 0.51, H * 0.24 + i * 40 - 22), s, font=self.board_font, fill=(255, 255, 255, 217))
        n = 2 if self.template == "debate" else 1
        for idx in range(n):
            speaking = line is not None and line["speaker"] == idx
            self._draw_char(frame, idx, t, amp if speaking else 0.0, self._viseme(line, t) if speaking else "X")
        d = ImageDraw.Draw(frame)
        if line and self.template != "lesson":
            rows = _wrap(d, line["text"], self.caption_font, W * 0.66)
            for i, s in enumerate(rows):
                y = H - 40 - (len(rows) - 1 - i) * 38 - 24
                d.text((W / 2 + 2, y + 2), s, font=self.caption_font, fill=(0, 0, 0, 200), anchor="mt")
                d.text((W / 2, y), s, font=self.caption_font, fill=(255, 255, 255, 255), anchor="mt")
        lw = d.textlength(self.disclaimer, font=self.label_font) + 28
        d.rounded_rectangle((24, 24, 24 + lw, 58), 17, fill=(255, 255, 255, 235))
        d.text((38, 41), self.disclaimer, font=self.label_font, fill=(10, 10, 10, 255), anchor="lm")
        d.text((W - 28, 41), "animind", font=self.brand_font, fill=(255, 255, 255, 102), anchor="rm")
        return frame

    def frames(self) -> Iterator[np.ndarray]:
        for i in range(max(1, math.ceil(self.duration_s * self.fps))):
            yield np.asarray(self.frame(i / self.fps).convert("RGB"))


def fit_scale(canvas_h: int, template: str, idx: int) -> float:
    places = PLACES.get(template, PLACES["solo"])
    return places[min(idx, len(places) - 1)][1] * (H * 0.92) / canvas_h
