"""Server compositor for contract v2 rigs, encoded by the Rigly renderer.

Same scene as the app's browser preview (``lib/engine.ts``), with the
cinematic look of :mod:`products.animind_core.look`: parallax scenario
plates with depth-of-field blur, rim/key light and soft shadows baked into
the rig layers, a camera that pushes in, drifts and reframes on the speaker
(cross-dissolves and whip pans between lines), a colour grade, a vignette,
captions and lower-thirds. Per frame: blink, breathing, the speaker's head
lifting with the voice and the mouth sprite of the current Rhubarb viseme.
The parody label is always baked in. Frames go to
:class:`animation.parametric.renderer.AnimationRenderer` (ffmpeg pipe).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from . import look
from .look import Light, Shot

W, H, FPS = look.W, look.H, 24
SS = 1.5  # characters are composited supersampled so close-ups stay sharp
_REPO = Path(__file__).resolve().parents[2]
_FONT_DIR = _REPO / "Fonts" / "Montserrat" / "static"
SCENARIOS = ("studio", "classroom", "arena", "space")
# (x centre as a fraction of W, scale on top of "fit to 92% of the height")
PLACES = {"debate": [(0.27, 0.85), (0.73, 0.85)], "lesson": [(0.25, 0.9)], "solo": [(0.5, 0.95)]}
DISSOLVE_S, WHIP_S, FADE_IN_S, FADE_OUT_S = 0.45, 0.3, 0.5, 0.6


def _font(weight: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = _FONT_DIR / f"Montserrat-{weight}.ttf"
    return ImageFont.truetype(str(path), size) if path.is_file() else ImageFont.load_default()


@dataclass
class RigView:
    """One view, layers pre-scaled (and pre-lit) to the stage scale."""

    size: tuple[int, int]
    layers: list[tuple[str, Image.Image, tuple[int, int], int]] = field(default_factory=list)
    visemes: dict[str, tuple[Image.Image, tuple[int, int], int]] = field(default_factory=dict)
    shadow: Image.Image | None = None
    shadow_pad: int = 0
    side: int = 1


def load_rig_view(view: dict[str, Any], scale: float, light: Light | None = None) -> RigView:
    """``view`` is a v2 CharacterView whose ``url`` fields are file paths; ``scale`` is frame-space."""
    scale *= SS
    cw, ch = view["canvas"]["w"], view["canvas"]["h"]
    size = (round(cw * scale), round(ch * scale))

    def sized(layer: dict[str, Any], rim: bool) -> tuple[Image.Image, tuple[int, int]]:
        with Image.open(layer["url"]) as im:
            img = im.convert("RGBA")
        w, h = max(1, round(layer["w"] * scale)), max(1, round(layer["h"] * scale))
        img, xy = img.resize((w, h), Image.LANCZOS), (round(layer["x"] * scale), round(layer["y"] * scale))
        return (look.lit_layer(img, xy, size, light, rim) if light else img), xy

    rv = RigView(size=size, side=light.side if light else 1)
    for layer in sorted(view["layers"], key=lambda l: l["z"]):
        img, xy = sized(layer, rim=layer["name"] in {"body", "head", "hair"})
        rv.layers.append((layer["name"], img, xy, layer["z"]))
    for v, layer in view["visemes"].items():
        img, xy = sized(layer, rim=False)
        rv.visemes[v] = (img, xy, layer.get("z", 99))
    if light:
        radius = 9 * SS
        rv.shadow = look.drop_shadow([(i, xy) for n, i, xy, _ in rv.layers if n in {"body", "head"}], size, radius)
        rv.shadow_pad = int(radius * 3)
    return rv


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


def _fade(img: Image.Image, alpha: float) -> Image.Image:
    if alpha >= 0.999:
        return img
    out = img.copy()
    out.putalpha(img.getchannel("A").point(lambda v: int(v * alpha)))
    return out


def _tracked(d: ImageDraw.ImageDraw, xy: tuple[float, float], text: str, font: Any, fill: tuple, track: float) -> float:
    """Letter-spaced text; returns the advance."""
    x, y = xy
    for ch in text:
        d.text((x, y), ch, font=font, fill=fill)
        x += d.textlength(ch, font=font) + track
    return x - xy[0]


def pretty_name(name: str) -> str:
    name = " ".join((name or "").split())
    name = name[:1].upper() + name[1:]
    return name if len(name) <= 34 else name[:33].rstrip() + "…"


class Scene:
    """Iterates RGB frames for one render job."""

    def __init__(self, *, template: str, scenario: str, rigs: list[dict[str, RigView] | None], script: list[dict[str, Any]],
                 envelope: np.ndarray, env_fps: int, duration_s: float, disclaimer: str, fps: int = FPS,
                 names: list[str] | None = None, roles: list[str] | None = None, lang: str = "pt-BR") -> None:
        self.template = template if template in PLACES else "solo"
        self.scenario = scenario if scenario in SCENARIOS else "studio"
        self.rigs, self.script, self.env, self.env_fps = rigs, script, envelope, env_fps
        self.duration_s, self.disclaimer, self.fps = duration_s, disclaimer, fps
        self.lang = lang
        self.places = PLACES[self.template]
        self.far, self.mid = look.plates(self.scenario, self.template, [p[0] for p in self.places])
        self.lut = look.grade_lut(self.scenario)
        self.vig = look.vignette(0.55)
        self.scrim = look.scrim()
        self.accent = look.ACCENT[self.scenario]
        self.caption_font = _font("SemiBold", 34)
        self.quote_font = _font("SemiBoldItalic", 34)
        self.tag_font = _font("Bold", 15)
        self.source_font = _font("Medium", 17)
        self.label_font = _font("SemiBold", 18)
        self.brand_font = _font("Medium", 16)
        self.board_font = _font("Medium", 30)
        self.name_font = _font("Bold", 30)
        self.role_font = _font("SemiBold", 15)
        self.names, self.roles = names or [], roles or []
        self.shots = self._plan_shots()
        self._cache: dict[Any, Image.Image] = {}

    # ------------------------------------------------------------ timeline
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

    def shot_for(self, speaker: int, i: int) -> Shot:
        """Framing per template: wide, or a medium close-up with look room."""
        if self.template == "debate":
            if speaker < 0:
                return Shot(W / 2, H * 0.5, 1.06)
            px = self.places[min(speaker, 1)][0]
            look_room = 0.07 if px < 0.5 else -0.07
            return Shot(W * (px + look_room), H * 0.55, 1.42)
        if self.template == "lesson":
            return Shot(W / 2, H * 0.5, 1.05) if speaker < 0 or i % 2 == 0 else Shot(W / 2, H * 0.44, 1.16)
        return Shot(W / 2, H * 0.5, 1.08) if speaker < 0 or i % 2 == 0 else Shot(W / 2, H * 0.5, 1.34)

    def _plan_shots(self) -> list[tuple[float, Shot, str]]:
        """(start s, shot, transition into it). Debate opens wide, then cuts per speaker."""
        shots: list[tuple[float, Shot, str]] = [(0.0, look.clamp_shot(self.shot_for(-1, 0)), "cut")]
        kinds = ("dissolve", "whip")
        for i, line in enumerate(self.script):
            start = line["startMs"] / 1000
            if i == 0:
                if self.template != "debate":
                    continue
                start = min(start + 1.4, max(start, line["endMs"] / 1000 - 1.0))
            shot = look.clamp_shot(self.shot_for(line["speaker"], i))
            if shot != shots[-1][1]:
                shots.append((max(start - 0.12, shots[-1][0] + 0.5), shot, kinds[len(shots) % 2]))
        return shots

    def camera(self, t: float) -> list[tuple[float, Shot, float]]:
        """[(weight, shot, motion blur 0..1)] for time ``t`` (two entries during a dissolve)."""
        k = max(i for i, s in enumerate(self.shots) if s[0] <= t)
        start, shot, kind = self.shots[k]

        def pushed(s: Shot, since: float) -> Shot:
            return Shot(s.cx, s.cy, s.zoom * (1 + 0.045 * min(1.0, since / 7.0)))

        local = t - start
        cur = pushed(shot, local)
        if k == 0:
            return [(1.0, cur, 0.0)]
        p_start, prev, _ = self.shots[k - 1]
        before = pushed(prev, start - p_start)
        if kind == "dissolve" and local < DISSOLVE_S:
            a = look.ease(local / DISSOLVE_S)
            return [(1 - a, Shot(before.cx, before.cy, before.zoom * (1 + 0.01 * a)), 0.0), (a, cur, 0.0)]
        if kind == "whip" and local < WHIP_S:
            u = local / WHIP_S
            a = look.ease(u)
            s = Shot(before.cx + (cur.cx - before.cx) * a, before.cy + (cur.cy - before.cy) * a,
                     before.zoom + (cur.zoom - before.zoom) * a)
            return [(1.0, s, math.sin(math.pi * u))]
        return [(1.0, cur, 0.0)]

    # ------------------------------------------------------------ drawing
    def _draw_char(self, stage: Image.Image, idx: int, t: float, amp: float, vis: str) -> None:
        cx = W * self.places[min(idx, len(self.places) - 1)][0] * SS
        seed = 1.3 * idx
        breathe = math.sin(t * 1.6 + seed) * 2 * SS
        views = self.rigs[idx] if idx < len(self.rigs) else None
        rv = (views.get(self._view(idx, t)) or views.get("front")) if views else None
        if rv is None:
            d = ImageDraw.Draw(stage)
            lift = amp * 4 * SS
            d.ellipse((cx - 160 * SS, H * 0.6 * SS, cx + 160 * SS, H * 1.1 * SS), fill=(42, 42, 42, 255))
            d.ellipse((cx - 100 * SS, (H * 0.25) * SS - lift, cx + 100 * SS, H * 0.6 * SS - lift), fill=(51, 51, 51, 255))
            return
        x0, y0 = round(cx - rv.size[0] / 2), round(H * 0.98 * SS) - rv.size[1]
        if rv.shadow is not None:  # cast away from the key light, onto the backdrop
            stage.alpha_composite(rv.shadow, (x0 - rv.shadow_pad + round(rv.side * 26 * SS), y0 - rv.shadow_pad + round(10 * SS + breathe * 0.4)))
        blink = (t + seed) % 3.6 < 0.12
        mouth = rv.visemes.get(vis) or rv.visemes.get("X")
        parts = list(rv.layers) + ([("mouth", mouth[0], mouth[1], mouth[2])] if mouth else [])
        for name, img, (lx, ly), _z in sorted(parts, key=lambda p: p[3]):
            if (name == "eyes" and blink) or (name == "eyes_closed" and not blink):
                continue
            dy = breathe * 0.4 if name == "body" else breathe - amp * 4 * SS - (amp * 6 * SS if name == "brows" else 0)
            stage.alpha_composite(img, (x0 + lx, y0 + ly + round(dy)))

    def _board(self, stage: Image.Image, t: float) -> None:
        cur = self._line_at(t, 0.35) or (self.script[0] if self.script else None)
        key = ("board", cur["text"] if cur else "", cur.get("kind") if cur else None)
        if key not in self._cache:
            bw, bh = int(W * 0.44 * SS), int(H * 0.48 * SS)
            img = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            if self.scenario == "classroom":  # chalkboard in a wooden frame
                d.rounded_rectangle((0, 0, bw - 1, bh - 1), int(10 * SS), fill=(70, 46, 28, 255))
                d.rounded_rectangle((int(12 * SS), int(12 * SS), bw - int(12 * SS), bh - int(12 * SS)), int(6 * SS), fill=(24, 44, 36, 255))
            else:  # glass panel
                d.rounded_rectangle((0, 0, bw - 1, bh - 1), int(16 * SS), fill=(255, 255, 255, 16), outline=(255, 255, 255, 46), width=2)
            ink = (236, 238, 228, 235) if self.scenario == "classroom" else (255, 255, 255, 225)
            quote = bool(cur) and cur.get("kind") == "quote"
            x, top, width = W * 0.03 * SS, H * 0.05 * SS, W * 0.38 * SS
            if cur and cur.get("beat"):
                _tracked(d, (x, top), str(cur["beat"]).upper(), _font("Bold", int(18 * SS)), (*self.accent, 255), 3 * SS)
            src = (cur.get("quote") or {}).get("source", "") if quote else ""
            text = f"“{key[1]}”" if quote else key[1]
            room = bh - top - H * (0.16 if src else 0.08) * SS
            for size in (30, 27, 24, 21):  # largest size whose rows fit the board
                font = _font("MediumItalic" if quote else "Medium", int(size * SS))
                rows, step = _wrap(d, text, font, width), size * 1.42 * SS
                if len(rows) * step <= room:
                    break
            for i, s in enumerate(rows):
                d.text((x, top + H * 0.07 * SS + i * step), s, font=font, fill=ink)
            if src:
                sf = _font("Medium", int(16 * SS))
                src = "— " + src
                while d.textlength(src, font=sf) > width and len(src) > 8:
                    src = src.rstrip("…")[:-2].rstrip(" ,·") + "…"
                d.text((x, bh - H * 0.07 * SS), src, font=sf, fill=(*ink[:3], 170))
            self._cache[key] = img
        stage.alpha_composite(self._cache[key], (int(W * 0.48 * SS), int(H * 0.14 * SS)))

    def _stage(self, t: float, line: dict[str, Any] | None, amp: float) -> Image.Image:
        stage = Image.new("RGBA", (round(W * SS), round(H * SS)), (0, 0, 0, 0))
        if self.template == "lesson":
            self._board(stage, t)
        n = 2 if self.template == "debate" else 1
        order = sorted(range(n), key=lambda i: 1 if (line and line["speaker"] == i) else 0)  # speaker in front
        for idx in order:
            speaking = line is not None and line["speaker"] == idx
            self._draw_char(stage, idx, t, amp if speaking else 0.0, self._viseme(line, t) if speaking else "X")
        return stage

    def _shoot(self, stage: Image.Image, shot: Shot, drift: tuple[float, float, float], blur: float) -> Image.Image:
        out = self.far.transform((W, H), Image.AFFINE, look.affine(shot, "far", drift), Image.BILINEAR).convert("RGBA")
        out.alpha_composite(self.mid.transform((W, H), Image.AFFINE, look.affine(shot, "mid", drift), Image.BILINEAR))
        out.alpha_composite(stage.transform((W, H), Image.AFFINE, look.affine(shot, "stage", drift, SS), Image.BILINEAR))
        if blur > 0.05:  # whip pan: horizontal motion blur
            smear = out.resize((W // 40, H), Image.BOX).resize((W, H), Image.BICUBIC)
            out = Image.blend(out, smear, min(0.92, blur))
        return out

    def _pages(self, line: dict[str, Any]) -> list[list[str]]:
        """Caption rows in pages of up to two, quote text wrapped in “ ” and set in italics."""
        key = ("pages", line["text"], line.get("kind"))
        if key not in self._cache:
            quote = line.get("kind") == "quote"
            text = f"“{line['text']}”" if quote else line["text"]
            d = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
            rows = _wrap(d, text, self.quote_font if quote else self.caption_font, W * 0.72)
            self._cache[key] = [rows[i:i + 2] for i in range(0, len(rows), 2)] or [[]]
        return self._cache[key]

    def _page_at(self, line: dict[str, Any], t: float) -> int:
        """Which caption page is on screen: pages advance in proportion to their word count."""
        pages = self._pages(line)
        if len(pages) == 1:
            return 0
        start, end = line["startMs"] / 1000, line["endMs"] / 1000
        frac = (t - start) / max(0.1, end - start)
        sizes = [sum(len(r.split()) for r in p) for p in pages]
        acc = 0.0
        for i, n in enumerate(sizes):
            acc += n / max(1, sum(sizes))
            if frac < acc:
                return i
        return len(pages) - 1

    def _caption(self, line: dict[str, Any], page: int = 0) -> Image.Image:
        key = ("cap", line["text"], line.get("kind"), page)
        if key not in self._cache:
            font = self.quote_font if line.get("kind") == "quote" else self.caption_font
            rows = self._pages(line)[page]
            img = Image.new("RGBA", (W, 150), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            y0 = 150 - 20 - len(rows) * 44
            for i, s in enumerate(rows):
                d.text((W / 2, y0 + i * 44), s, font=font, fill=(0, 0, 0, 230), anchor="mt")
            shadow = img.filter(ImageFilter.GaussianBlur(5))
            d = ImageDraw.Draw(shadow)
            for i, s in enumerate(rows):
                d.text((W / 2, y0 + i * 44), s, font=font, fill=(255, 255, 255, 255), anchor="mt")
            self._cache[key] = shadow
        return self._cache[key]

    def _tag(self, line: dict[str, Any]) -> Image.Image | None:
        """Top-center chip: CITAÇÃO + source for a real quote, PARÁFRASE for written lines."""
        kind = line.get("kind")
        if kind not in ("quote", "paraphrase"):
            return None
        key = ("tag", kind, (line.get("quote") or line.get("basis") or {}).get("source", ""))
        if key not in self._cache:
            pt = self.lang == "pt-BR"
            if kind == "quote":
                head = "CITAÇÃO REAL" if pt else "REAL QUOTE"
                src = f"{line['quote'].get('source') or ''} · Wikiquote".lstrip(" ·")
            else:
                head = "PARÁFRASE" if pt else "PARAPHRASE"
                basis = (line.get("basis") or {}).get("source")
                src = (("inspirada em " if pt else "after ") + basis) if basis else ("fala escrita para a cena" if pt else "written for the scene")
            d = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
            while d.textlength(src, font=self.source_font) > 560 and len(src) > 12:
                src = src[:-2].rstrip(" ,·") + "…" if not src.endswith("…") else src[:-3].rstrip(" ,·") + "…"
            hw = sum(d.textlength(c, font=self.tag_font) + 2.0 for c in head)
            sw = d.textlength(src, font=self.source_font)
            w = int(hw + sw + 66)
            img = Image.new("RGBA", (w, 40), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            d.rounded_rectangle((0, 0, w - 1, 39), 20, fill=(8, 8, 10, 175))
            dot = (*self.accent, 255) if kind == "quote" else (200, 200, 205, 255)
            d.ellipse((16, 15, 26, 25), fill=dot)
            _tracked(d, (36, 12), head, self.tag_font, dot, 2.0)
            d.text((36 + hw + 12, 20), src, font=self.source_font, fill=(255, 255, 255, 220), anchor="lm")
            self._cache[key] = img
        return self._cache[key]

    def _lower_third(self, idx: int) -> Image.Image:
        key = ("lt", idx)
        if key not in self._cache:
            name = pretty_name(self.names[idx]) if idx < len(self.names) else ""
            role = (self.roles[idx] if idx < len(self.roles) else "").upper()
            img = Image.new("RGBA", (620, 96), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            nw = d.textlength(name, font=self.name_font)
            rw = sum(d.textlength(c, font=self.role_font) + 2.2 for c in role)
            pw = int(min(620, max(nw, rw) + 52))
            panel = Image.new("RGBA", img.size, (0, 0, 0, 0))
            ImageDraw.Draw(panel).rounded_rectangle((0, 0, pw, 92), 10, fill=(8, 8, 10, 170))
            img.alpha_composite(panel)
            d.rounded_rectangle((0, 0, 6, 92), 3, fill=(*self.accent, 255))
            d.text((26, 14), name, font=self.name_font, fill=(255, 255, 255, 255))
            _tracked(d, (27, 58), role, self.role_font, (*self.accent, 235), 2.2)
            self._cache[key] = img.crop((0, 0, pw + 1, 96))
        return self._cache[key]

    def _overlays(self, frame: Image.Image, t: float, line: dict[str, Any] | None) -> None:
        frame.alpha_composite(self.scrim)
        if line and self.template != "lesson":
            since = t - line["startMs"] / 1000
            a = look.ease(since / 0.18)
            frame.alpha_composite(_fade(self._caption(line, self._page_at(line, t)), a), (0, H - 150 + round(6 * (1 - a))))
        if line and (tag := self._tag(line)) is not None:
            a = look.ease((t - line["startMs"] / 1000) / 0.25)
            frame.alpha_composite(_fade(tag, a), ((W - tag.width) // 2, 22 - round(8 * (1 - a))))
        if line:
            first = next(l for l in self.script if l["speaker"] == line["speaker"])
            since = t - line["startMs"] / 1000
            if first is line and line["speaker"] < len(self.names) and 0.25 <= since:
                hold = min(3.4, (line["endMs"] - line["startMs"]) / 1000)
                a = look.ease((since - 0.25) / 0.35) * (1 - look.ease((since - hold) / 0.3))
                if a > 0.01:
                    lt = self._lower_third(line["speaker"])
                    right = self.template == "debate" and line["speaker"] == 1
                    slide = round(30 * (1 - a)) * (1 if right else -1)
                    x = (W - 64 - lt.width if right else 64) + slide
                    frame.alpha_composite(_fade(lt, a), (x, H - 236))
        d = ImageDraw.Draw(frame)
        lw = d.textlength(self.disclaimer, font=self.label_font) + 28
        d.rounded_rectangle((24, 24, 24 + lw, 58), 17, fill=(255, 255, 255, 235))
        d.text((38, 41), self.disclaimer, font=self.label_font, fill=(10, 10, 10, 255), anchor="lm")
        d.text((W - 28, 41), "animind", font=self.brand_font, fill=(255, 255, 255, 110), anchor="rm")

    def frame(self, t: float) -> Image.Image:
        line = self._line_at(t)
        k = min(len(self.env) - 1, max(0, int(t * self.env_fps)))
        amp = float(self.env[k]) if len(self.env) else 0.0
        stage = self._stage(t, line, amp)
        drift = look.handheld(t)
        out = None
        for weight, shot, blur in self.camera(t):
            img = self._shoot(stage, shot, drift, blur)
            out = img if out is None else Image.blend(out, img, weight)
        rgb = ImageChops.multiply(out.convert("RGB").point(self.lut), self.vig)
        fade = look.ease(t / FADE_IN_S) * look.ease((self.duration_s - t) / FADE_OUT_S)
        if fade < 0.999:
            rgb = Image.blend(Image.new("RGB", (W, H)), rgb, fade)
        frame = rgb.convert("RGBA")
        self._overlays(frame, t, line)
        return frame

    def frames(self) -> Iterator[np.ndarray]:
        for i in range(max(1, math.ceil(self.duration_s * self.fps))):
            yield np.asarray(self.frame(i / self.fps).convert("RGB"))


def fit_scale(canvas_h: int, template: str, idx: int) -> float:
    places = PLACES.get(template, PLACES["solo"])
    return places[min(idx, len(places) - 1)][1] * (H * 0.92) / canvas_h


def light(scenario: str, template: str, idx: int) -> Light:
    places = PLACES.get(template, PLACES["solo"])
    return look.light_for(scenario, template, idx, places[min(idx, len(places) - 1)][0])
