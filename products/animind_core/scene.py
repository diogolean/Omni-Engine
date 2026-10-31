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
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterator

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from . import eyes, frameqa, look, styles
from .look import Light, Shot

W, H, FPS = look.W, look.H, 24
# Viseme changes are hard cuts: each mouth sprite has its own alpha shape, so an alpha crossfade between
# two shapes shows both mouths at once (the "two mouths" frames). Smoothness comes from the cue timing
# (render.smooth_visemes), not from blending sprites.
XFADE_S = 0.0


def viseme_mix(cues: list[dict[str, Any]], t: float, fade: float = XFADE_S) -> tuple[str, str, float]:
    """``(current, previous, weight of current)`` at ``t``: the newest cue fades in over ``fade`` s."""
    cur, prev, since = "X", "X", 1e9
    for cue in cues:
        if cue["t"] > t:
            break
        prev, cur, since = cur, cue["v"], t - cue["t"]
    return cur, prev, 1.0 if cur == prev or fade <= 0 else min(1.0, max(0.0, since / fade))
SS = 2.0  # characters are composited supersampled so close-ups stay sharp
_REPO = Path(__file__).resolve().parents[2]
_FONT_DIR = _REPO / "Fonts" / "Montserrat" / "static"
SCENARIOS = ("studio", "classroom", "arena", "space")
# (x centre as a fraction of W, scale on top of BUST_H)
PLACES = {"debate": [(0.29, 1.0), (0.71, 1.0)], "lesson": [(0.25, 1.0)], "solo": [(0.5, 1.06)]}
# Set geometry (fractions of H / W). The rigs are busts: each stands behind a lectern whose top hides
# the cut, so in a wide shot the floor, the lectern base and its contact shadow show the full depth.
BUST_H, BUST_BOTTOM = 0.55, 0.8
LECTERN_TOP, LECTERN_BASE, LECTERN_W = 0.645, 0.835, 0.27
FACE_Y = 0.42  # where the faces sit, for close framings
BOARD = (0.45, 0.08, 0.94, 0.62)  # lesson board on the stage (x0, y0, x1, y1)
DISSOLVE_S, WHIP_S, FADE_IN_S, FADE_OUT_S = 0.45, 0.3, 0.5, 0.6
CAP_H = 180  # caption strip height (speaker label + two rows)
V_FACE_AT = 0.34  # 9:16: faces sit this high in the output so the chin clears the captions (cap_y 0.70)
V_CU_FACE_AT = 0.29  # close-ups: bigger head, so it sits higher still
VWIN = H * 9 / 16  # vertical camera window width in frame px at zoom 1


def _font(weight: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = _FONT_DIR / f"Montserrat-{weight}.ttf"
    return ImageFont.truetype(str(path), size) if path.is_file() else ImageFont.load_default()


BREATH_SHARE = {"body": 0.4, "torso": 0.4, "neck": 0.7}  # head, eyes, mouth: 1.0


@dataclass
class RigView:
    """One view, layers pre-scaled (and pre-lit) to the stage scale."""

    size: tuple[int, int]
    layers: list[tuple[str, Image.Image, tuple[int, int], int]] = field(default_factory=list)
    visemes: dict[str, tuple[Image.Image, tuple[int, int], int]] = field(default_factory=dict)
    side: int = 1
    eye_shift: tuple[float, float] = (0.0, 0.0)  # iris travel (stage px) for a gaze of +-1


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
    if view.get("eyeRig"):
        mx, my = view["eyeRig"]["maxShift"]
        rv.eye_shift = (mx * scale, my * scale)
    for layer in sorted(view["layers"], key=lambda l: l["z"]):
        img, xy = sized(layer, rim=layer["name"] in {"body", "torso", "neck", "head", "hair"})
        rv.layers.append((layer["name"], img, xy, layer["z"]))
    for v, layer in view["visemes"].items():
        img, xy = sized(layer, rim=False)
        rv.visemes[v] = (img, xy, layer.get("z", 99))
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
                 names: list[str] | None = None, roles: list[str] | None = None, lang: str = "pt-BR",
                 fmt: str = "16:9", style: str = "cartoon") -> None:
        # 9:16 shoots a vertical window (VWIN frame px wide) of the same 16:9 set at 1080x1920.
        self.vertical = fmt == "9:16"
        self.OW, self.OH = (1080, 1920) if self.vertical else (W, H)
        self.win = VWIN if self.vertical else W
        k = 1.45 if self.vertical else 1.0
        self.k = k
        self.cap_h = round(CAP_H * k)
        self.cap_y = round(self.OH * 0.70) if self.vertical else H - CAP_H  # vertical: above the app chrome
        self.template = template if template in PLACES else "solo"
        self.scenario = scenario if scenario in SCENARIOS else "studio"
        self.rigs, self.script, self.env, self.env_fps = rigs, script, envelope, env_fps
        self.duration_s, self.disclaimer, self.fps = duration_s, disclaimer, fps
        self.lang = lang
        self.places = PLACES[self.template]
        self.style = styles.pick(style)
        self.far, self.mid = look.plates(self.scenario, self.template, [p[0] for p in self.places])
        self.far, self.mid = styles.tint_plate(self.far, self.style), styles.tint_plate(self.mid, self.style)
        self.lut = styles.lut(look.grade_lut(self.scenario), self.style)
        self.sat = styles.saturation(self.style)
        self.texture = styles.texture(self.style, (self.OW, self.OH))
        self.vig = look.vignette(0.55, (self.OW, self.OH))
        self.scrim = look.scrim((self.OW, self.OH), 0.62 if self.vertical else 0.68)
        self.accent = look.ACCENT[self.scenario]
        self.caption_font = _font("SemiBold", round(34 * k))
        self.quote_font = _font("SemiBoldItalic", round(34 * k))
        self.tag_font = _font("Bold", 15)
        self.source_font = _font("Medium", 17)
        self.label_font = _font("SemiBold", 18)
        self.brand_font = _font("Medium", 16)
        self.board_font = _font("Medium", 30)
        self.name_font = _font("Bold", 30)
        self.role_font = _font("SemiBold", 15)
        self.speaker_font = _font("Bold", round(16 * k))
        self.names, self.roles = names or [], roles or []
        self.shots = self._plan_shots()
        self._cache: dict[Any, Image.Image] = {}
        self.trace: list[dict[str, Any]] = []
        self._mouth_log: list[dict[str, Any]] = []
        self._mouth_ref: dict[Any, Any] = {}
        n = 2 if self.template == "debate" else 1
        self.lights = [look.light_for(self.scenario, self.template, i, self.places[min(i, len(self.places) - 1)][0]) for i in range(n)]

    # ------------------------------------------------------------ timeline
    def _line_at(self, t: float, grace: float = 0.0) -> dict[str, Any] | None:
        ms = t * 1000
        return next((l for l in self.script if l["startMs"] <= ms <= l["endMs"] + grace * 1000), None)

    def _viseme(self, line: dict[str, Any], t: float) -> tuple[str, str, float]:
        return viseme_mix(line.get("visemes") or [], t, XFADE_S)

    def _view(self, idx: int, t: float) -> str:
        line = self._line_at(t, 0.35)
        if line and line["speaker"] == idx and line.get("view"):
            return line["view"]
        if self.template == "debate":
            return "right" if idx == 0 else "left"
        return "front"

    def shot_for(self, speaker: int, i: int) -> Shot:
        """Shot grammar per line: a wide establishing shot, over-the-shoulder singles, a close-up
        cutaway on every real quote and a two-shot for the closing (debate); the lesson cuts to
        the board for quotes."""
        line = self.script[i] if 0 <= i < len(self.script) else {}
        quote = line.get("kind") == "quote"
        last = i == len(self.script) - 1 and len(self.script) >= 4
        if speaker < 0:
            return Shot(W / 2, H * 0.5, 1.0, "wide")
        px = self.places[min(speaker, len(self.places) - 1)][0]
        toward = 1 if px < 0.5 else -1  # look room toward the other side of the frame
        if self.vertical:  # singles only: the window is too narrow for a two-shot
            if quote:
                return Shot(W * (px + 0.01 * toward), vertical_cy(1.55, V_CU_FACE_AT), 1.55, "cu")
            return Shot(W * (px + 0.02 * toward), vertical_cy(1.22), 1.22, "single")
        if self.template == "debate":
            if last:
                return Shot(W / 2, H * 0.47, 1.24, "two")
            if quote:
                return Shot(W * (px + 0.035 * toward), H * FACE_Y, 2.1, "cu")
            return Shot(W * (px + 0.115 * toward), H * 0.47, 1.6, "ots")
        if self.template == "lesson":
            if quote:
                bx0, by0, bx1, by1 = BOARD
                return Shot(W * (bx0 + bx1) / 2, H * (by0 + by1) / 2 + H * 0.03, 1.72, "board")
            if last:
                return Shot(W / 2, H * 0.5, 1.0, "wide")
            return Shot(W * (px + 0.13), H * 0.47, 1.55, "mcu")
        if quote:
            return Shot(W / 2, H * FACE_Y, 2.0, "cu")
        return Shot(W / 2, H * 0.5, 1.0, "wide") if last else Shot(W / 2, H * 0.47, 1.5, "mcu")

    def _plan_shots(self) -> list[tuple[float, Shot, str]]:
        """(start s, shot, transition into it). Every template opens wide (establishing), then cuts
        to the first speaker; hard cuts between singles, a dissolve into wide/two/board shots and a
        whip pan when a quote close-up answers the other side."""
        if self.vertical:
            return self._plan_vertical()
        shots: list[tuple[float, Shot, str]] = [(0.0, look.clamp_shot(self.shot_for(-1, 0)), "cut")]
        for i, line in enumerate(self.script):
            start = line["startMs"] / 1000
            if i == 0:
                start = min(start + 1.6, max(start, line["endMs"] / 1000 - 1.0))
            shot = look.clamp_shot(self.shot_for(line["speaker"], i))
            prev = shots[-1][1]
            if shot != prev:
                if shot.kind in ("wide", "two", "board") or prev.kind in ("wide", "board"):
                    kind = "dissolve"
                elif shot.kind == "cu" and abs(shot.cx - prev.cx) > W * 0.25:
                    kind = "whip"
                else:
                    kind = "cut"
                shots.append((max(start - 0.12, shots[-1][0] + 0.5), shot, kind))
        return shots

    def _plan_vertical(self) -> list[tuple[float, Shot, str]]:
        """Shot/countershot for 9:16: open on the first speaker (no establishing wide), hard cut on
        every change of speaker, and on long debate lines a short reaction cutaway to the listener
        (who keeps looking at the speaker)."""
        clamp = lambda s: look.clamp_shot(s, self.win)  # noqa: E731
        first = self.script[0]["speaker"] if self.script else 0
        shots: list[tuple[float, Shot, str]] = [(0.0, clamp(self.shot_for(max(0, first), 0)), "cut")]
        for i, line in enumerate(self.script):
            start, end = line["startMs"] / 1000, line["endMs"] / 1000
            shot = clamp(self.shot_for(line["speaker"], i))
            if shot != shots[-1][1]:
                shots.append((max(start - 0.1, shots[-1][0] + 0.5), shot, "cut"))
            if self.template == "debate" and end - start > 5.5:
                other = 1 - line["speaker"]
                mid = start + (end - start) * 0.55
                shots.append((mid, clamp(replace(self.shot_for(other, i), kind="react")), "cut"))
                shots.append((mid + 1.3, shot, "cut"))
        return shots

    def camera(self, t: float) -> list[tuple[float, Shot, float]]:
        """[(weight, shot, motion blur 0..1)] for time ``t`` (two entries during a dissolve)."""
        k = max(i for i, s in enumerate(self.shots) if s[0] <= t)
        start, shot, kind = self.shots[k]

        def pushed(s: Shot, since: float) -> Shot:
            return Shot(s.cx, s.cy, s.zoom * (1 + 0.045 * min(1.0, since / 7.0)), s.kind)

        local = t - start
        cur = pushed(shot, local)
        if k == 0:
            return [(1.0, cur, 0.0)]
        p_start, prev, _ = self.shots[k - 1]
        before = pushed(prev, start - p_start)
        if kind == "dissolve" and local < DISSOLVE_S:
            # a glide, not a cross-dissolve: blending two framings of the same stage doubles every face
            a = look.ease(local / DISSOLVE_S)
            return [(1.0, Shot(before.cx + (cur.cx - before.cx) * a, before.cy + (cur.cy - before.cy) * a,
                               before.zoom + (cur.zoom - before.zoom) * a, cur.kind), 0.0)]
        if kind == "whip" and local < WHIP_S:
            u = local / WHIP_S
            a = look.ease(u)
            s = Shot(before.cx + (cur.cx - before.cx) * a, before.cy + (cur.cy - before.cy) * a,
                     before.zoom + (cur.zoom - before.zoom) * a, cur.kind)
            return [(1.0, s, math.sin(math.pi * u))]
        return [(1.0, cur, 0.0)]

    # ------------------------------------------------------------ drawing
    def _soft(self, idx: int, view: str, rv: RigView) -> Image.Image:
        """The listener in an over-the-shoulder single: out of focus and darker (cached rest pose)."""
        key = ("soft", idx, view)
        if key not in self._cache:
            img = Image.new("RGBA", rv.size, (0, 0, 0, 0))
            mouth = rv.visemes.get("X")
            parts = list(rv.layers) + ([("mouth", mouth[0], mouth[1], mouth[2])] if mouth else [])
            for name, layer, xy, _z in sorted(parts, key=lambda p: p[3]):
                if name not in ("eyes_closed", "eyes_half", "eye_white", "iris", "eye_socket"):
                    img.alpha_composite(layer, xy)
            img = img.filter(ImageFilter.GaussianBlur(7 * SS))
            r, g, b, a = img.split()
            dim = lambda v: int(v * 0.62)  # noqa: E731
            self._cache[key] = Image.merge("RGBA", (r.point(dim), g.point(dim), b.point(dim), a))
        return self._cache[key]

    def _set_piece(self, name: str, idx: int, soft: bool) -> tuple[Image.Image, tuple[int, int]]:
        """Lectern (or its contact shadow) for place ``idx``, in stage pixels."""
        key = (name, idx, soft)
        if key not in self._cache:
            cx = W * self.places[min(idx, len(self.places) - 1)][0] * SS
            lw = round(W * LECTERN_W * SS)
            lh = round(H * (LECTERN_BASE - LECTERN_TOP) * SS)
            if name == "lectern":
                img = look.lectern(self.scenario, (lw, lh), self.lights[min(idx, len(self.lights) - 1)])
                if soft:
                    img = img.filter(ImageFilter.GaussianBlur(6 * SS))
                xy = (round(cx - lw / 2), round(H * LECTERN_TOP * SS))
            else:  # contact shadow on the floor, pushed away from the key
                sw, sh = round(lw * 1.35), round(H * 0.06 * SS)
                img = look.contact_shadow((sw, sh))
                side = self.lights[min(idx, len(self.lights) - 1)].side
                xy = (round(cx - sw / 2 + side * W * 0.012 * SS), round(H * LECTERN_BASE * SS - sh * 0.55))
            self._cache[key] = (img, xy)
        return self._cache[key]

    def _draw_char(self, stage: Image.Image, idx: int, t: float, amp: float, vis: tuple[str, str, float], soft: bool = False) -> None:
        cx = W * self.places[min(idx, len(self.places) - 1)][0] * SS
        seed = 1.3 * idx
        breathe = math.sin(t * 1.6 + seed) * 2 * SS
        views = self.rigs[idx] if idx < len(self.rigs) else None
        view = self._view(idx, t)
        rv = (views.get(view) or views.get("front")) if views else None
        bottom = H * BUST_BOTTOM * SS
        if rv is None:
            d = ImageDraw.Draw(stage)
            lift = amp * 4 * SS
            top = bottom - H * BUST_H * SS
            d.ellipse((cx - 150 * SS, top + H * 0.3 * SS, cx + 150 * SS, bottom + H * 0.2 * SS), fill=(42, 42, 42, 255))
            d.ellipse((cx - 85 * SS, top - lift, cx + 85 * SS, top + H * 0.32 * SS - lift), fill=(51, 51, 51, 255))
            return
        x0, y0 = round(cx - rv.size[0] / 2), round(bottom) - rv.size[1]
        if soft:
            stage.alpha_composite(self._soft(idx, view, rv), (x0, y0 + round(breathe * 0.4)))
            return
        lids = eyes.blink(t, 7 + idx * 13)
        gx, gy = self.gaze(idx, t, view)
        gx, gy = max(-1.0, min(1.0, gx)), max(-1.0, min(1.0, gy))  # eye_shift is the socket-safe maximum
        cur_v, prev_v, wv = vis
        mouth = rv.visemes.get(cur_v) or rv.visemes.get("X")
        parts = list(rv.layers) + ([("mouth", mouth[0], mouth[1], mouth[2])] if mouth else [])
        before = rv.visemes.get(prev_v) or rv.visemes.get("X")
        if XFADE_S > 0 and mouth and before and wv < 1.0 and before[0] is not mouth[0]:
            fading = mouth[0].copy()  # only valid when all visemes share one alpha shape; off by default
            fading.putalpha(fading.getchannel("A").point(lambda a: int(a * wv)))
            parts = list(rv.layers) + [("mouth", before[0], before[1], before[2]), ("mouth", fading, mouth[1], mouth[2] + 0.001)]
        mouths = [p for p in parts if p[0] == "mouth"]
        if mouths:  # frame trace for the video QA: how many mouth layers were drawn, and where
            m0 = mouths[-1]
            bx = m0[1].getchannel("A").point(lambda a: 255 if a > 128 else 0).getbbox() or (0, 0, *m0[1].size)
            ox, oy = x0 + m0[2][0], y0 + m0[2][1]
            self._mouth_log.append({"char": idx, "v": cur_v, "layers": len(mouths), "view": view,
                                    "box": (ox + bx[0], oy + bx[1], ox + bx[2], oy + bx[3])})
        names = {p[0] for p in parts}
        movable = {"eye_white", "iris", "eye_socket"} <= names
        socket = next((p for p in parts if p[0] == "eye_socket"), None)
        for name, img, (lx, ly), _z in sorted(parts, key=lambda p: p[3]):
            if name == "eye_socket" or (movable and name == "eyes"):
                continue
            if (name == "eyes" and lids == "closed") or (name == "eyes_closed" and lids != "closed") \
                    or (name == "eyes_half" and lids != "half"):
                continue
            # The face no longer rides the jaw: only the mouth layer changes with speech.
            dy = breathe * BREATH_SHARE.get(name, 1.0) - (amp * 2 * SS if name == "brows" else 0)
            if name == "iris" and socket is not None:
                img, (lx, ly) = self._iris(img, (lx, ly), socket, rv.eye_shift, gx, gy)
            stage.alpha_composite(img, (x0 + lx, y0 + ly + round(dy)))
        if mouths:  # reference: the one mouth as drawn, measured the way the video QA measures frames
            key = (idx, view, cur_v)
            if key not in self._mouth_ref:
                box = self._mouth_log[-1]["box"]
                crop = np.asarray(stage.crop(box).convert("RGB"))
                self._mouth_ref[key] = frameqa.measure(crop, (0, 0, crop.shape[1], crop.shape[0]))
            self._mouth_log[-1]["ref"] = self._mouth_ref[key]

    def gaze(self, idx: int, t: float, view: str) -> tuple[float, float]:
        """Where character ``idx`` looks: at the other speaker by default, sometimes at the lens
        (more in the front view and on close-ups), short wanders; see :mod:`eyes`."""
        line = self._line_at(t, 0.35)
        shot = self.camera(t)[-1][1]
        if self.template == "debate":
            toward = 1 if self.places[min(idx, len(self.places) - 1)][0] < 0.5 else -1
        elif self.template == "lesson":
            toward = 1  # the board is on the right
        else:
            toward = 0
        bias = 0.0
        if line and line["speaker"] == idx:
            bias += 0.3 if shot.kind == "cu" else 0.08
        if self.template == "solo":
            bias += 0.35
        facing = {"left": -1, "right": 1}.get(view, 0)
        return eyes.gaze(t, 101 + idx * 57, toward=toward, view=view, facing=facing, camera_bias=bias)

    def _iris(self, iris: Image.Image, xy: tuple[int, int], socket: tuple, shift: tuple[float, float],
              gx: float, gy: float) -> tuple[Image.Image, tuple[int, int]]:
        """The iris layer moved by the gaze and clipped to the eye opening."""
        _n, sock, (sx, sy), _z = socket
        out = Image.new("RGBA", sock.size, (0, 0, 0, 0))
        out.paste(iris, (xy[0] - sx + round(gx * shift[0]), xy[1] - sy + round(gy * shift[1])))
        out.putalpha(ImageChops.multiply(out.getchannel("A"), sock.getchannel("A")))
        return out, (sx, sy)

    def _board(self, stage: Image.Image, t: float, soft: bool = False) -> None:
        """Lesson board: beat label, the line set as large as the board allows (vertically
        centred), and the quote's source at the foot."""
        cur = self._line_at(t, 0.35) or (self.script[0] if self.script else None)
        key = ("board", cur["text"] if cur else "", cur.get("kind") if cur else None)
        bx0, by0, bx1, by1 = BOARD
        if key not in self._cache:
            bw, bh = int(W * (bx1 - bx0) * SS), int(H * (by1 - by0) * SS)
            pad = int(18 * SS)
            img = Image.new("RGBA", (bw + 2 * pad, bh + 2 * pad), (0, 0, 0, 0))
            shadow = Image.new("L", img.size, 0)  # soft shadow on the wall behind the board
            ImageDraw.Draw(shadow).rounded_rectangle((pad + 6 * SS, pad + 10 * SS, pad + bw, pad + bh + 4 * SS), int(12 * SS), fill=150)
            img.putalpha(shadow.filter(ImageFilter.GaussianBlur(9 * SS)))
            d = ImageDraw.Draw(img)
            o = pad
            if self.scenario == "classroom":  # chalkboard in a wooden frame, with a chalk tray
                d.rounded_rectangle((o, o, o + bw - 1, o + bh - 1), int(10 * SS), fill=(82, 54, 32, 255))
                d.rounded_rectangle((o + int(14 * SS), o + int(14 * SS), o + bw - int(14 * SS), o + bh - int(14 * SS)), int(5 * SS), fill=(26, 48, 39, 255))
                d.rectangle((o + int(30 * SS), o + bh - int(14 * SS), o + bw - int(30 * SS), o + bh - int(4 * SS)), fill=(104, 70, 42, 255))
                ink = (238, 240, 230, 240)
            else:  # dark glass screen with a thin accent frame
                d.rounded_rectangle((o, o, o + bw - 1, o + bh - 1), int(14 * SS), fill=(10, 12, 16, 228), outline=(*self.accent, 150), width=int(2 * SS))
                ink = (255, 255, 255, 240)
            quote = bool(cur) and cur.get("kind") == "quote"
            x, width = o + bw * 0.075, bw * 0.85
            top = o + bh * 0.1
            if cur and cur.get("beat"):
                _tracked(d, (x, top), str(cur["beat"]).upper(), _font("Bold", int(17 * SS)), (*self.accent, 255), 3.5 * SS)
                d.line((x, top + 30 * SS, x + 44 * SS, top + 30 * SS), fill=(*self.accent, 220), width=int(3 * SS))
            src = (cur.get("quote") or {}).get("source", "") if quote else ""
            text = f"“{key[1]}”" if quote else key[1]
            body_top, body_bot = top + 50 * SS, o + bh - bh * (0.17 if src else 0.09)
            room = body_bot - body_top
            for size in (52, 48, 44, 40, 37, 34, 31, 28, 25, 22):  # largest size whose rows fit the board
                font = _font("SemiBoldItalic" if quote else "SemiBold", int(size * SS))
                rows, step = _wrap(d, text, font, width), size * 1.3 * SS
                if len(rows) * step <= room:
                    break
            y = body_top + max(0.0, (room - len(rows) * step) / 2)
            for i, row in enumerate(rows):
                d.text((x, y + i * step), row, font=font, fill=ink)
            if src:
                sf = _font("Medium", int(17 * SS))
                src = "— " + src
                while d.textlength(src, font=sf) > width and len(src) > 8:
                    src = src.rstrip("…")[:-2].rstrip(" ,·") + "…"
                d.text((x, o + bh - bh * 0.1), src, font=sf, fill=(*ink[:3], 175), anchor="lm")
            self._cache[key] = img
        board = self._cache[key]
        if soft:  # out of focus behind the teacher in a medium close-up
            if ("soft",) + key not in self._cache:
                self._cache[("soft",) + key] = board.filter(ImageFilter.GaussianBlur(5 * SS))
            board = self._cache[("soft",) + key]
        pad = (board.width - int(W * (bx1 - bx0) * SS)) // 2
        stage.alpha_composite(board, (int(W * bx0 * SS) - pad, int(H * by0 * SS) - pad))

    def _stage(self, t: float, line: dict[str, Any] | None, amp: float, shot: Shot | None = None) -> Image.Image:
        stage = Image.new("RGBA", (round(W * SS), round(H * SS)), (0, 0, 0, 0))
        if self.template == "lesson":
            self._board(stage, t, soft=shot is not None and shot.kind == "mcu")
        n = 2 if self.template == "debate" else 1
        for idx in range(n):
            img, xy = self._set_piece("shadow", idx, False)
            stage.alpha_composite(img, xy)
        order = sorted(range(n), key=lambda i: 1 if (line and line["speaker"] == i) else 0)  # speaker in front
        single = shot is not None and shot.kind in ("ots", "cu") and n > 1
        for idx in order:
            speaking = line is not None and line["speaker"] == idx
            soft = single and line is not None and not speaking
            self._draw_char(stage, idx, t, amp if speaking else 0.0, self._viseme(line, t) if speaking else ("X", "X", 1.0), soft)
            img, xy = self._set_piece("lectern", idx, soft)
            stage.alpha_composite(img, xy)
        return stage

    def _aff(self, shot: Shot, depth: str, drift: tuple[float, float, float], scale: float = 1.0) -> tuple[float, ...]:
        """PIL affine (output px -> layer px) for a depth plane."""
        a, b, c, d, e, f = look.affine(shot, depth, drift, scale)
        if not self.vertical:
            return a, b, c, d, e, f
        # output px -> 16:9 frame px of a centred VWIN-wide window, then the usual mapping
        u, ox = H / self.OH, (W - self.win) / 2
        return a * u, b * u, a * ox + c, d * u, e * u, d * ox + f

    def _to_output(self, box: tuple[float, float, float, float], shot: Shot, drift: tuple[float, float, float]) -> list[int]:
        """A stage-px box in output px (inverse of the stage affine)."""
        a, b, c, d, e, f = self._aff(shot, "stage", drift, SS)
        m = np.linalg.inv(np.array([[a, b, c], [d, e, f], [0, 0, 1]], dtype=np.float64))
        pts = [m @ np.array([x, y, 1.0]) for x in (box[0], box[2]) for y in (box[1], box[3])]
        xs, ys = [q[0] for q in pts], [q[1] for q in pts]
        return [round(min(xs)), round(min(ys)), round(max(xs)), round(max(ys))]

    def _shoot(self, stage: Image.Image, shot: Shot, drift: tuple[float, float, float], blur: float) -> Image.Image:
        size = (self.OW, self.OH)

        def aff(depth: str, scale: float = 1.0) -> tuple[float, ...]:
            return self._aff(shot, depth, drift, scale)

        out = self.far.transform(size, Image.AFFINE, aff("far"), Image.BILINEAR).convert("RGBA")
        out.alpha_composite(self.mid.transform(size, Image.AFFINE, aff("mid"), Image.BILINEAR))
        out.alpha_composite(stage.transform(size, Image.AFFINE, aff("stage", SS), Image.BILINEAR))
        if blur > 0.05:  # whip pan: horizontal motion blur
            smear = out.resize((self.OW // 40, self.OH), Image.BOX).resize(size, Image.BICUBIC)
            out = Image.blend(out, smear, min(0.92, blur))
        return out

    def _pages(self, line: dict[str, Any]) -> list[list[str]]:
        """Caption rows in pages of up to two, quote text wrapped in “ ” and set in italics."""
        key = ("pages", line["text"], line.get("kind"))
        if key not in self._cache:
            quote = line.get("kind") == "quote"
            text = f"“{line['text']}”" if quote else line["text"]
            d = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
            rows = _wrap(d, text, self.quote_font if quote else self.caption_font, self.OW * (0.86 if self.vertical else 0.72))
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
        """Bottom caption: a small tracked speaker label in the accent colour over one or two rows."""
        key = ("cap", line["text"], line.get("kind"), page, line.get("speaker"))
        if key not in self._cache:
            font = self.quote_font if line.get("kind") == "quote" else self.caption_font
            rows = self._pages(line)[page]
            img = Image.new("RGBA", (self.OW, self.cap_h), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            y0 = self.cap_h - round(22 * self.k) - len(rows) * round(44 * self.k)
            sp = line.get("speaker", -1)
            label = pretty_name(self.names[sp]).upper() if 0 <= sp < len(self.names) else ""
            lw = sum(d.textlength(c, font=self.speaker_font) + 2.6 for c in label)
            for i, s in enumerate(rows):
                d.text((self.OW / 2, y0 + i * round(44 * self.k)), s, font=font, fill=(0, 0, 0, 230), anchor="mt")
            if label:
                d.text((self.OW / 2, y0 - round(26 * self.k)), label, font=self.speaker_font, fill=(0, 0, 0, 200), anchor="mt")
            shadow = img.filter(ImageFilter.GaussianBlur(5))
            d = ImageDraw.Draw(shadow)
            if label:
                _tracked(d, (self.OW / 2 - lw / 2, y0 - round(26 * self.k)), label, self.speaker_font, (*self.accent, 255), 2.6)
            for i, s in enumerate(rows):
                d.text((self.OW / 2, y0 + i * round(44 * self.k)), s, font=font, fill=(255, 255, 255, 255), anchor="mt")
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
            self._cache[key] = self._big(img)
        return self._cache[key]

    def _big(self, img: Image.Image) -> Image.Image:
        """Chips and lower-thirds are drawn at 16:9 size; 9:16 scales them up to its text size."""
        return img.resize((round(img.width * self.k), round(img.height * self.k)), Image.LANCZOS) if self.vertical else img

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
            self._cache[key] = self._big(img.crop((0, 0, pw + 1, 96)))
        return self._cache[key]

    def _overlays(self, frame: Image.Image, t: float, line: dict[str, Any] | None, shot: Shot | None = None) -> None:
        frame.alpha_composite(self.scrim)
        on_board = self.template == "lesson" and (shot is None or shot.kind in ("board", "wide"))
        if line and not on_board:  # the lesson reads the board; captions only when it is out of frame
            since = t - line["startMs"] / 1000
            a = look.ease(since / 0.18)
            frame.alpha_composite(_fade(self._caption(line, self._page_at(line, t)), a), (0, self.cap_y + round(6 * (1 - a))))
        if line and (tag := self._tag(line)) is not None:
            a = look.ease((t - line["startMs"] / 1000) / 0.25)
            frame.alpha_composite(_fade(tag, a), ((self.OW - tag.width) // 2, (110 if self.vertical else 22) - round(8 * (1 - a))))
        if line:
            first = next(l for l in self.script if l["speaker"] == line["speaker"])
            since = t - line["startMs"] / 1000
            if first is line and line["speaker"] < len(self.names) and 0.25 <= since:
                hold = min(3.4, (line["endMs"] - line["startMs"]) / 1000)
                a = look.ease((since - 0.25) / 0.35) * (1 - look.ease((since - hold) / 0.3))
                if a > 0.01:
                    lt = self._lower_third(line["speaker"])
                    # wide/two: under the speaker; close debate shots: on the open side, off the face
                    close = shot is not None and shot.kind in ("ots", "cu")
                    right = self.template == "debate" and (line["speaker"] == 1) != close
                    slide = round(30 * (1 - a)) * (1 if right else -1)
                    x = (self.OW - 64 - lt.width if right else 64) + slide
                    frame.alpha_composite(_fade(lt, a), (x, self.cap_y - round(82 * self.k)))
        d = ImageDraw.Draw(frame)
        lw = d.textlength(self.disclaimer, font=self.label_font) + 28
        d.rounded_rectangle((24, 24, 24 + lw, 58), 17, fill=(255, 255, 255, 235))
        d.text((38, 41), self.disclaimer, font=self.label_font, fill=(10, 10, 10, 255), anchor="lm")
        d.text((self.OW - 28, 41), "animind", font=self.brand_font, fill=(255, 255, 255, 110), anchor="rm")

    def frame(self, t: float) -> Image.Image:
        line = self._line_at(t)
        k = min(len(self.env) - 1, max(0, int(t * self.env_fps)))
        amp = float(self.env[k]) if len(self.env) else 0.0
        cam = self.camera(t)
        shot = cam[-1][1]
        self._mouth_log: list[dict[str, Any]] = []
        stage = self._stage(t, line, amp, shot)
        drift = look.handheld(t)
        self.trace.append({"t": round(t, 3), "takes": len(cam), "blur": round(max(c[2] for c in cam), 2),
                           "mouths": [m | {"box": self._to_output(m["box"], shot, drift)} for m in self._mouth_log]})
        out = None
        for weight, shot, blur in cam:
            img = self._shoot(stage, shot, drift, blur)
            out = img if out is None else Image.blend(out, img, weight)
        rgb = out.convert("RGB").point(self.lut)
        if abs(self.sat - 1.0) > 1e-3:
            rgb = ImageEnhance.Color(rgb).enhance(self.sat)
        rgb = ImageChops.multiply(rgb, self.vig)
        if self.texture is not None:
            rgb = rgb.convert("RGBA")
            rgb.alpha_composite(self.texture)
            rgb = rgb.convert("RGB")
        fade = look.ease(t / FADE_IN_S) * look.ease((self.duration_s - t) / FADE_OUT_S)
        if fade < 0.999:
            rgb = Image.blend(Image.new("RGB", (self.OW, self.OH)), rgb, fade)
        frame = rgb.convert("RGBA")
        self._overlays(frame, t, line, cam[-1][1])
        return frame

    def frames(self) -> Iterator[np.ndarray]:
        for i in range(max(1, math.ceil(self.duration_s * self.fps))):
            yield np.asarray(self.frame(i / self.fps).convert("RGB"))


def vertical_cy(zoom: float, face_at: float = V_FACE_AT) -> float:
    """Camera centre (frame px) that puts the faces at ``face_at`` of a 9:16 output at ``zoom``."""
    return H * FACE_Y + (0.5 - face_at) * H / zoom


def fit_scale(canvas_h: int, template: str, idx: int) -> float:
    places = PLACES.get(template, PLACES["solo"])
    return places[min(idx, len(places) - 1)][1] * (H * BUST_H) / canvas_h


def light(scenario: str, template: str, idx: int) -> Light:
    places = PLACES.get(template, PLACES["solo"])
    return look.light_for(scenario, template, idx, places[min(idx, len(places) - 1)][0])
