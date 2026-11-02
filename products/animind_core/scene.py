"""Server compositor for contract v2 rigs, encoded by the Rigly renderer.

Same scene as the app's browser preview (``lib/engine.ts``), with the
cinematic look of :mod:`products.animind_core.look`: parallax scenario
plates with a zoom-dependent depth of field (close-ups throw the set out of focus), rim/key light and soft shadows baked into
the rig layers, a camera that pushes in, drifts and reframes on the speaker
(eased push-ins, glides and whip pans between lines), a colour grade, a highlight bloom, a vignette
centred on the subject,
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

from . import eyes, frameqa, languages, look, styles, titles
from .look import Light, Shot

W, H, FPS = look.W, look.H, 24
# Viseme changes are hard cuts: each mouth sprite has its own alpha shape, so an alpha crossfade between
# two shapes shows both mouths at once (the "two mouths" frames). Smoothness comes from the cue timing
# (render.smooth_visemes), not from blending sprites.
XFADE_S = 0.0


_LAYER_IMAGES: dict[str, Image.Image] = {}


def layer_image(img: Image.Image | str) -> Image.Image:
    """A rig layer is a PIL image, or a file path from a cached puppet."""
    if isinstance(img, Image.Image):
        return img
    path = str(img)
    cached = _LAYER_IMAGES.get(path)
    if cached is None:
        with Image.open(path) as opened:
            cached = opened.convert("RGBA")
        _LAYER_IMAGES[path] = cached
    return cached


def _ink_top(img: Image.Image) -> int | None:
    rows = np.flatnonzero((np.asarray(img.getchannel("A")) > 128).any(axis=1))
    return int(rows[0]) if rows.size else None


def rest_lip_drop(rv: RigView, mouth: tuple, face_w: int = 0) -> int:
    """Shift so an open mouth's upper lip sits on the rest (X) mouth's upper lip.

    The cached rig already places X on the real mouth. A mustache layer that also
    holds the coat, or a white moustache the face detector reads as eyes, is ignored.
    """
    rest = rv.visemes.get("X")
    if rest is None:
        return 0
    r_img = structured_mouth(rest[0], face_w, open_shape=False)
    r_top, m_top = _ink_top(r_img), _ink_top(layer_image(mouth[1]))
    if r_top is None or m_top is None:
        return 0
    return (rest[1][1] + r_top) - (mouth[2][1] + m_top)


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
SCENARIOS = ("studio", "classroom", "arena", "space", "agora")
# (x centre as a fraction of W, scale on top of BUST_H)
PLACES = {"debate": [(0.29, 1.0), (0.71, 1.0)], "lesson": [(0.25, 1.0)], "solo": [(0.5, 1.06)]}
# Set geometry (fractions of H / W). The rigs are busts: each stands behind a lectern whose top hides
# the cut, so in a wide shot the floor, the lectern base and its contact shadow show the full depth.
BUST_H, BUST_BOTTOM = 0.55, 0.8
LECTERN_TOP, LECTERN_BASE, LECTERN_W = 0.645, 0.835, 0.27
FACE_Y = 0.42  # where the faces sit, for close framings
BOARD = (0.45, 0.08, 0.94, 0.62)  # lesson board on the stage (x0, y0, x1, y1)
SPEAKER_PULL, FLOOR_EASE_S = 0.55, 1.0  # wide/two shots: how far the light leans to the speaker, how fast
KEY_GLOW = (255, 206, 150)  # warm key pool screened over the subject
DISSOLVE_S, WHIP_S, FADE_IN_S, FADE_OUT_S = 0.45, 0.3, 0.25, 0.6
PUSH, PUSH_CU = 0.04, 0.075  # push-in over a shot (fraction of its zoom); quote close-ups lean in further
CU_Y = 0.40  # close-ups look higher so the hair keeps headroom above the frame
CU_ZOOM = 1.68  # 2.1 clipped the top of tall hair (Einstein) on a 16:9 close-up
# The opening frame is the thumbnail and the first thing a viewer sees: it starts at this brightness
# (scenario and characters already visible), never on black.
FADE_IN_FLOOR = 0.85
CAP_H = 180  # caption strip height (speaker label + two rows)
CAPTION_PLATE_A = 120  # opacity of the soft plate behind caption rows
V_FACE_AT = 0.34  # 9:16: faces sit this high in the output so the chin clears the captions (cap_y 0.70)
V_CU_FACE_AT = 0.40  # close-ups sit lower than 0.29, which pushed the hair off the top
V_CU_ZOOM = 1.35
VWIN = H * 9 / 16  # vertical camera window width in frame px at zoom 1


def _font(weight: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = _FONT_DIR / f"Montserrat-{weight}.ttf"
    return ImageFont.truetype(str(path), size) if path.is_file() else ImageFont.load_default()


BREATH_SHARE = {"body": 0.4, "torso": 0.4, "neck": 0.7, "arm_left": 0.45, "arm_right": 0.45}
BODY_LAYERS = ("body", "torso", "neck", "arm_left", "arm_right")
# per-line emotion -> brow lift in stage px (negative = up), eased in over EMO_EASE_S
EMO_BROW = {"neutro": 0.0, "alegre": -1.5, "irritado": 2.5, "surpreso": -4.0, "triste": -2.0, "desdenhoso": 1.2, "pensativo": -0.8}
EMO_EASE_S = 0.3
# (left y, right y, left rotation, right rotation), gently blended at line edges.
EMO_BROW_POSE = {
    "neutro": (0.0, 0.0, 0.0, 0.0),
    "alegre": (-1.5, -1.5, -4.0, 4.0),
    "irritado": (2.5, 2.5, 12.0, -12.0),
    "bravo": (2.5, 2.5, 12.0, -12.0),
    "triste": (-1.0, -1.0, -10.0, 10.0),
    "surpreso": (-4.0, -4.0, 1.0, -1.0),
    "pensativo": (-1.8, 0.6, -5.0, 4.0),
    "desdenhoso": (-2.0, 1.0, -8.0, 5.0),
    "ironico": (-2.0, 1.0, -8.0, 5.0),
    "irônico": (-2.0, 1.0, -8.0, 5.0),
}
EMO_ARM_ROT = {"alegre": (-5.0, 5.0), "irritado": (7.0, -7.0), "bravo": (7.0, -7.0),
                "triste": (4.0, -4.0), "surpreso": (-10.0, 10.0), "pensativo": (-3.0, -8.0),
                "desdenhoso": (-8.0, 2.0), "ironico": (-8.0, 2.0), "irônico": (-8.0, 2.0)}
EMO_MOUTH = {
    "neutro": "neutral", "alegre": "happy", "irritado": "angry", "bravo": "angry",
    "triste": "sad", "surpreso": "surprised", "pensativo": "thoughtful",
    "desdenhoso": "ironic", "ironico": "ironic", "irônico": "ironic",
}


_MOUTH_OF_FACE = 0.30
F_FACE_SHARE = 0.55  # F's mouth is about half an open vowel's width


def _open_colours() -> dict[str, tuple[int, int, int, int]]:
    return {
        "outline": (40, 24, 26, 255),
        "cavity": (52, 24, 26, 255),
        "teeth": (246, 242, 236, 255),
        "tongue": (190, 86, 92, 255),
        "lip": (150, 72, 70, 255),
    }


def scale_mouth_to_face(sprite: Image.Image, face_w: int, *, open_shape: bool = True) -> Image.Image:
    """A cached Nietzsche mouth can be a few dark pixels on the chin. Grow an open viseme to the face.

    Closed rests (X, A) stay as drawn. A shut open-viseme such as front G is redrawn open.
    """
    from . import mouthset  # noqa: PLC0415

    box = sprite.getchannel("A").getbbox()
    if not box or face_w < 24 or not open_shape:
        return sprite
    ink_w, ink_h = box[2] - box[0], box[3] - box[1]
    target = max(12, int(face_w * _MOUTH_OF_FACE))
    tiny = ink_w < int(face_w * 0.16)
    shut = ink_h < max(4, int(ink_w * 0.22))
    blob = mouthset.flat_dark_blob(sprite)
    if not tiny and not shut and not blob:
        return sprite
    if shut or blob:
        drawn = mouthset._lipped_open(target, _open_colours())
    else:
        drawn = sprite.crop(box).resize((target, max(4, int(ink_h * target / max(1, ink_w)))), Image.LANCZOS)
    canvas = Image.new("RGBA", (max(sprite.width, drawn.width + 4), max(sprite.height, drawn.height + 4)), (0, 0, 0, 0))
    cx = (box[0] + box[2]) // 2
    cy = (box[1] + box[3]) // 2
    canvas.paste(drawn, (max(0, cx - drawn.width // 2), max(0, cy - drawn.height // 2)), drawn)
    return canvas


def tuck_mustache_over_mouth(mouth: Image.Image, head: Image.Image, mouth_xy: tuple[int, int],
                             head_xy: tuple[int, int]) -> Image.Image:
    """Paint the head's mustache across the top of an open mouth so it is not a sticker on the hair."""
    sprite = mouth.copy()
    arr = np.array(sprite)
    hair = np.asarray(head)
    box = sprite.getchannel("A").getbbox()
    if not box:
        return sprite
    top = box[1] + int((box[3] - box[1]) * 0.22)
    for y in range(box[1], top):
        for x in range(box[0], box[2]):
            sx = mouth_xy[0] + x - head_xy[0]
            sy = mouth_xy[1] + y - head_xy[1]
            if not (0 <= sx < hair.shape[1] and 0 <= sy < hair.shape[0]):
                continue
            pixel = hair[sy, sx]
            if pixel[3] < 128:
                continue
            lum = float(pixel[:3].mean())
            chroma = int(pixel[:3].max()) - int(pixel[:3].min())
            if lum < 90 and chroma < 40:
                arr[y, x] = pixel
    return Image.fromarray(arr)


def structured_mouth(img: Image.Image | str, face_w: int = 0, *, open_shape: bool = True) -> Image.Image:
    """Load a path mouth, scale a chin-dot up to the face, and replace a flat dark oval.

    This runs while the frame is drawn, including mouths already saved on a puppet.
    """
    from . import mouthset  # noqa: PLC0415

    sprite = layer_image(img)
    if face_w:
        sprite = scale_mouth_to_face(sprite, face_w, open_shape=open_shape)
    if not open_shape or not mouthset.flat_dark_blob(sprite):
        return sprite
    box = sprite.getchannel("A").getbbox()
    if not box:
        return sprite
    width = max(12, int(face_w * _MOUTH_OF_FACE) if face_w else box[2] - box[0])
    drawn = mouthset._lipped_open(width, _open_colours())
    canvas = Image.new("RGBA", sprite.size, (0, 0, 0, 0))
    x = box[0] + (box[2] - box[0] - drawn.width) // 2
    y = box[1] + (box[3] - box[1] - drawn.height) // 2
    canvas.paste(drawn, (max(0, x), max(0, y)), drawn)
    return canvas


def _qa_box(box: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """Mouth box padded with a skin margin: a drawn mouth's outline fills its own bbox, so the frame QA
    needs surrounding skin to tell the cavity from the crop edge (reference and frames use the same box)."""
    x0, y0, x1, y1 = box
    px, py = max(3, round((x1 - x0) * 0.2)), max(3, round((y1 - y0) * 0.35))
    return (x0 - px, y0 - py, x1 + px, y1 + py)


@dataclass
class RigView:
    """One view, layers pre-scaled (and pre-lit) to the stage scale."""

    size: tuple[int, int]
    layers: list[tuple[str, Image.Image, tuple[int, int], int]] = field(default_factory=list)
    visemes: dict[str, tuple[Image.Image, tuple[int, int], int]] = field(default_factory=dict)
    expressions: dict[str, tuple[Image.Image, tuple[int, int], int]] = field(default_factory=dict)
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
    for emotion, layer in view.get("expressions", {}).items():
        img, xy = sized(layer, rim=False)
        rv.expressions[emotion] = (img, xy, layer.get("z", 99))
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
                 fmt: str = "16:9", style: str = "cartoon", topic: str = "") -> None:
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
        if self.template == "debate" and len(rigs) > 2:
            count = len(rigs)
            scale = max(0.68, 1.0 - 0.09 * (count - 2))
            self.places = [(0.16 + 0.68 * i / (count - 1), scale) for i in range(count)]
        else:
            self.places = PLACES[self.template]
        self.style = styles.pick(style)
        self.far, self.mid = look.plates(self.scenario, self.template, [p[0] for p in self.places])
        self.far, self.mid = styles.tint_plate(self.far, self.style), styles.tint_plate(self.mid, self.style)
        self.far_dof, self.mid_dof = look.dof_stack(self.far, look.DOF_FAR), look.dof_stack(self.mid, look.DOF_MID)
        self.lut = styles.lut(look.grade_lut(self.scenario), self.style)
        self.sat = styles.saturation(self.style)
        self.texture = styles.texture(self.style, (self.OW, self.OH))
        self.vig = look.vignette(0.55, (self.OW, self.OH))
        self.vig_field = look.vignette_field(0.6, (self.OW, self.OH))
        self.glow_field = look.key_glow_field(KEY_GLOW, 0.09, (self.OW, self.OH))
        self.bloom = look.BLOOM.get(self.scenario, 0.2)
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
        self.topic = topic
        self._plan_titles()
        self.shots = self._plan_shots()
        self._cache: dict[Any, Image.Image] = {}
        self.trace: list[dict[str, Any]] = []
        self._mouth_log: list[dict[str, Any]] = []
        self._face_log: list[dict[str, Any]] = []
        self._mouth_ref: dict[Any, Any] = {}
        n = len(self.rigs)
        self.lights = [look.light_for(self.scenario, self.template, i, self.places[min(i, len(self.places) - 1)][0]) for i in range(n)]

    # ------------------------------------------------------------ titles
    def _plan_titles(self) -> None:
        """Cold-open title while the voices have not started (``render`` leaves ``titles.TITLE_S``), act
        breaks in the silent gaps that open a new act, an end card after the last line."""
        s = self.script
        first = s[0]["startMs"] / 1000 if s else 0.0
        self.title_end = first if first >= 1.0 else 0.0
        last = s[-1]["endMs"] / 1000 if s else 0.0
        self.end_start = last + 0.3 if s and self.duration_s - last >= 1.5 else None
        self.matte = titles.matte_h(self.OW, self.OH)
        self.breaks: list[tuple[int, str, float, float, str]] = []  # (line, act, gap start, gap end, numeral)
        seen = [s[0].get("act")] if s and s[0].get("act") else []
        for i, act in titles.acts(s):
            a, b = s[i - 1]["endMs"] / 1000, s[i]["startMs"] / 1000
            seen.append(act)
            if b - a >= 0.6:
                self.breaks.append((i, act, a, b, titles.ROMAN[min(len(seen), len(titles.ROMAN)) - 1]))
        self.dips = [(a, b) for _i, _act, a, b, _n in self.breaks]

    def _title_card(self, end: bool = False) -> Image.Image:
        key = ("title", end)
        if key not in self._cache:
            lang = self.lang if self.lang in languages.PROFILES else None
            names = "  ×  ".join(pretty_name(n).upper() for n in self.names[: 2 if self.template == "debate" else 1] if n)
            kicker = (
                (languages.profile(lang).end if end else titles.KIND[lang].get(self.template, "").upper())
                if lang else ""
            )
            band = 0.785 if self.vertical else 0.757  # lower band: under the faces, inside the matte
            self._cache[key] = titles.title_card((self.OW, self.OH), self.k * (0.86 if not self.vertical else 1.0), kicker,
                                                 names, self.topic, self.accent, band)
        return self._cache[key]

    def matte_at(self, t: float) -> float:
        """Letterbox amount 0..1: on for the cold open and the end card, eased off/on."""
        m = 0.0
        if self.title_end:
            m = 1.0 - look.ease((t - self.title_end) / titles.MATTE_EASE_S)
        if self.end_start is not None:
            m = max(m, look.ease((t - self.end_start) / titles.MATTE_EASE_S))
        return m

    def _titles(self, rgb: Image.Image, t: float) -> Image.Image:
        """Matte bars, cold-open title and end card, under the fade (they fade in and out with the picture)."""
        m = self.matte_at(t)
        bar = round(self.matte * m)
        a_title = 1.0 - look.ease((t - (self.title_end - 0.3)) / 0.4) if self.title_end else 0.0
        a_end = look.ease((t - self.end_start - 0.25) / 0.5) if self.end_start is not None else 0.0
        if bar <= 0 and a_title <= 0.01 and a_end <= 0.01:
            return rgb
        frame = rgb.convert("RGBA")
        if bar > 0:
            d = ImageDraw.Draw(frame)
            d.rectangle((0, 0, self.OW, bar - 1), fill=(0, 0, 0, 255))
            d.rectangle((0, self.OH - bar, self.OW, self.OH), fill=(0, 0, 0, 255))
        if a_title > 0.01:
            frame.alpha_composite(_fade(self._title_card(), a_title))
        if a_end > 0.01:
            frame.alpha_composite(_fade(self._title_card(end=True), a_end))
        return frame.convert("RGB")

    def _chapter(self, frame: Image.Image, t: float) -> None:
        """Chapter mark for the act that just started, top left, from the dip's lowest point."""
        for _i, act, a, b, numeral in self.breaks:
            t0 = (a + b) / 2
            if not t0 <= t <= t0 + titles.CHAPTER_HOLD_S + 0.5:
                continue
            al = look.ease((t - t0) / 0.4) * (1 - look.ease((t - t0 - titles.CHAPTER_HOLD_S) / 0.5))
            if al <= 0.01:
                continue
            key = ("chapter", act, numeral)
            if key not in self._cache:
                label = titles.ACT_LABEL[self.lang].get(act, act) if self.lang in languages.PROFILES else ""
                self._cache[key] = titles.chapter_card(self.k, numeral, label, self.accent)
            card = self._cache[key]
            frame.alpha_composite(_fade(card, al), (24 - round(10 * (1 - al)), round(190 if self.vertical else 66)))

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
        return self._mouth_above_captions(self._shot_for(speaker, i, quote, last), speaker)

    def _mouth_above_captions(self, shot: Shot, speaker: int) -> Shot:
        """Keep the rest mouth above the caption scrim on a tall head, without cropping the crown."""
        rv = (self.rigs[speaker] or {}).get("front") if speaker < len(self.rigs) else None
        rest = rv.visemes.get("X") if rv else None
        if self.vertical or shot.kind not in ("cu", "single", "ots", "mcu") or rest is None:
            return shot
        mouth_y = (H * BUST_BOTTOM * SS - rv.size[1] + rest[1][1] + layer_image(rest[0]).height) / SS
        head_top = (H * BUST_BOTTOM * SS - rv.size[1]) / SS
        limit = self.cap_y - 64
        margin = 12.0
        z = look.clamp_shot(shot).zoom * (1 + PUSH_CU)
        floor = 1.04 * (1 + PUSH_CU)
        cy = shot.cy
        for _ in range(6):
            cy = max(shot.cy, mouth_y - (limit - H / 2) / z)
            # Lowering the camera (larger cy) pushes the crown out. Zoom out until it fits.
            need = cy - head_top + margin
            if need <= 0:
                break
            z_fit = H / (2 * need)
            if z <= z_fit + 1e-3:
                break
            z = max(floor, z_fit)
        cy_max = head_top - (margin - H / 2) / z
        cy = min(cy, cy_max)
        zoom = shot.zoom
        full = look.clamp_shot(shot).zoom * (1 + PUSH_CU)
        if z + 1e-6 < full:
            zoom = max(1.04, z / (1 + PUSH_CU))
        return replace(shot, cy=cy, zoom=zoom)

    def _shot_for(self, speaker: int, i: int, quote: bool, last: bool) -> Shot:
        px = self.places[min(speaker, len(self.places) - 1)][0]
        toward = 1 if px < 0.5 else -1  # look room toward the other side of the frame
        if self.vertical:  # singles only: the window is too narrow for a two-shot
            if quote:
                return Shot(W * (px + 0.01 * toward), vertical_cy(V_CU_ZOOM, V_CU_FACE_AT), V_CU_ZOOM, "cu")
            return Shot(W * (px + 0.02 * toward), vertical_cy(1.22, 0.40), 1.22, "single")
        if self.template == "debate":
            if last:
                return Shot(W / 2, H * 0.47, 1.24, "two")
            if quote:
                return Shot(W * (px + 0.035 * toward), H * CU_Y, CU_ZOOM, "cu")
            if i % 4 == 2:
                return Shot(W * (px + 0.06 * toward), H * CU_Y, 1.62, "cu")
            if i > 0 and i % 4 == 0:
                return Shot(W * (px + 0.08 * toward), H * 0.44, 1.55, "single")
            if i > 0 and i % 4 == 3:
                return Shot(W / 2, H * 0.47, 1.24, "two")
            return Shot(W * (px + 0.115 * toward), H * 0.47, 1.6, "ots")
        if self.template == "lesson":
            if quote:
                bx0, by0, bx1, by1 = BOARD
                return Shot(W * (bx0 + bx1) / 2, H * (by0 + by1) / 2 + H * 0.03, 1.72, "board")
            if last:
                return Shot(W / 2, H * 0.5, 1.0, "wide")
            return Shot(W * (px + 0.13), H * 0.47, 1.55, "mcu")
        if quote:
            return Shot(W / 2, H * CU_Y, CU_ZOOM, "cu")
        return Shot(W / 2, H * 0.5, 1.0, "wide") if last else Shot(W / 2, H * 0.47, 1.5, "mcu")

    def _plan_shots(self) -> list[tuple[float, Shot, str]]:
        """(start s, shot, transition into it). Every template opens wide (establishing), then cuts
        to the first speaker; hard cuts between singles, a dissolve into wide/two/board shots and a
        whip pan when a quote close-up answers the other side."""
        if self.vertical:
            return self._plan_vertical()
        shots: list[tuple[float, Shot, str]] = [(0.0, look.clamp_shot(self.shot_for(-1, 0)), "cut")]

        def add(start: float, shot: Shot, transition: str) -> None:
            shot = look.clamp_shot(shot)
            if shot != shots[-1][1]:
                shots.append((max(start, shots[-1][0] + 0.5), shot, transition))

        for i, line in enumerate(self.script):
            start = line["startMs"] / 1000
            end = line["endMs"] / 1000
            if i == 0:  # after a cold-open title the wide has already established the set
                start = min(start + (0.8 if self.title_end else 1.6), max(start, line["endMs"] / 1000 - 1.0))
            shot = look.clamp_shot(self.shot_for(line["speaker"], i))
            prev = shots[-1][1]
            act_cut = self._act_cut(i)
            if act_cut is not None and shot != prev:  # a new act: hard cut at the bottom of the dip
                add(act_cut, shot, "cut")
            elif shot != prev:
                if shot.kind in ("wide", "two", "board") or prev.kind in ("wide", "board"):
                    kind = "dissolve"
                elif shot.kind == "cu" and abs(shot.cx - prev.cx) > W * 0.25:
                    kind = "whip"
                else:
                    kind = "cut"
                add(start - 0.12, shot, kind)
            # Deterministic speech-paced inserts. Long lines briefly reveal the
            # listener's reaction or restore spatial context, then return to
            # the speaker. This provides reaction, countershot and two-shot
            # coverage without an LLM or random camera decisions.
            span = end - line["startMs"] / 1000
            if self.template == "debate" and len(self.rigs) > 1 and span >= 3.6:
                insert_at = line["startMs"] / 1000 + span * (0.52 if i % 2 else 0.6)
                if i % 3 == 1:
                    other = (line["speaker"] + 1) % len(self.rigs)
                    px = self.places[other][0]
                    toward = 1 if px < 0.5 else -1
                    insert = Shot(W * (px + 0.07 * toward), H * 0.46, 1.76, "react")
                else:
                    insert = Shot(W / 2, H * 0.47, 1.22, "two")
                add(insert_at, insert, "cut")
                if insert_at + 1.15 < end - 0.25:
                    add(insert_at + 1.05, shot, "cut")
        return shots

    def camera_plan(self) -> list[dict[str, Any]]:
        """Serializable deterministic edit decision list for UI and review sheets."""
        return [
            {
                "startMs": round(start * 1000),
                "kind": shot.kind,
                "transition": transition,
                "center": [round(shot.cx, 2), round(shot.cy, 2)],
                "zoom": round(shot.zoom, 3),
            }
            for start, shot, transition in self.shots
        ]

    def _act_cut(self, i: int) -> float | None:
        """Cut time for line ``i`` when it opens a new act (the middle of the dip), else None."""
        return next(((a + b) / 2 for j, _act, a, b, _n in self.breaks if j == i), None)

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
                act_cut = self._act_cut(i)
                shots.append((max(start - 0.1 if act_cut is None else act_cut, shots[-1][0] + 0.5), shot, "cut"))
            if self.template == "debate" and end - start > 5.5:
                other = (line["speaker"] + 1) % max(1, len(self.rigs))
                mid = start + (end - start) * 0.55
                shots.append((mid, clamp(replace(self.shot_for(other, i), kind="react")), "cut"))
                shots.append((mid + 1.3, shot, "cut"))
        return shots

    def camera(self, t: float) -> list[tuple[float, Shot, float]]:
        """[(weight, shot, motion blur 0..1)] for time ``t`` (two entries during a dissolve)."""
        k = max(i for i, s in enumerate(self.shots) if s[0] <= t)
        start, shot, kind = self.shots[k]

        def pushed(j: int, since: float) -> Shot:
            """Slow push-in over the whole shot, eased in and out (a quote close-up pushes further),
            so the camera never starts or stops with a jolt."""
            s_start, s, _ = self.shots[j]
            end = self.shots[j + 1][0] if j + 1 < len(self.shots) else self.duration_s
            span = max(2.0, end - s_start)
            amount = PUSH_CU if s.kind in ("cu", "board") else PUSH
            amount *= min(1.0, span / 4.0)  # brief reaction inserts should not rush a full-shot push
            return Shot(s.cx, s.cy, s.zoom * (1 + amount * look.ease(since / span)), s.kind)

        local = t - start
        cur = pushed(k, local)
        if k == 0:
            return [(1.0, cur, 0.0)]
        p_start, prev, _ = self.shots[k - 1]
        before = pushed(k - 1, start - p_start)
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
                    img.alpha_composite(layer_image(layer), xy)
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
            sy = y0 + round(breathe * 0.4)
            stage.alpha_composite(self._soft(idx, view, rv), (x0, sy))
            mouth = rv.visemes.get("X")
            if mouth:
                sprite = layer_image(mouth[0])
                bb = sprite.getchannel("A").point(lambda a: 255 if a > 128 else 0).getbbox() or (
                    0, 0, *sprite.size
                )
                box = _qa_box((
                    x0 + mouth[1][0] + bb[0], sy + mouth[1][1] + bb[1],
                    x0 + mouth[1][0] + bb[2], sy + mouth[1][1] + bb[3],
                ))
                key = (idx, view, "X", "soft")
                if key not in self._mouth_ref:
                    crop = np.asarray(stage.crop(box).convert("RGB"))
                    self._mouth_ref[key] = frameqa.measure(crop, (0, 0, crop.shape[1], crop.shape[0]))
                self._mouth_log.append(
                    {"char": idx, "v": "X", "layers": 1, "view": view, "box": box, "ref": self._mouth_ref[key]}
                )
            return
        lids = eyes.blink(t, 7 + idx * 13)
        # head micro-movements: slow sway + a small nod with the speech envelope (whole head group, mouth included)
        hx = round((math.sin(t * 0.63 + seed * 2.1) * 0.9 + math.sin(t * 1.37 + seed) * 0.4) * SS)
        hy = round(amp * 1.5 * SS)
        emotion, blend = self._emotion(idx, t)
        brow_pose = tuple(v * blend for v in EMO_BROW_POSE.get(emotion, EMO_BROW_POSE["neutro"]))
        arm_pose = tuple(v * blend for v in EMO_ARM_ROT.get(emotion, (0.0, 0.0)))
        brow = self._brow(idx, t) * SS  # legacy rigs keep their single combined brow layer
        self._face_log.append({"char": idx, "blink": lids, "emotion": emotion,
                               "brows": [round(v, 2) for v in brow_pose]})
        gx, gy = self.gaze(idx, t, view)
        gx, gy = max(-1.0, min(1.0, gx)), max(-1.0, min(1.0, gy))  # eye_shift is the socket-safe maximum
        cur_v, prev_v, wv = vis
        expression = EMO_MOUTH.get(emotion, "neutral")
        mouth = (
            rv.expressions.get(expression)
            if cur_v == "X"
            else None
        ) or rv.visemes.get(cur_v) or rv.visemes.get("X")
        mouth_tag = f"X_{expression}" if cur_v == "X" and expression in rv.expressions else cur_v
        head = next((part for part in rv.layers if part[0] == "head"), None)
        face_w = layer_image(head[1]).width if head is not None else 0
        if mouth is not None:
            grow_w = round(face_w * F_FACE_SHARE) if cur_v == "F" else face_w
            mouth = (structured_mouth(mouth[0], grow_w, open_shape=cur_v not in "XA"), mouth[1], mouth[2])
        parts = list(rv.layers) + ([("mouth", mouth[0], mouth[1], mouth[2])] if mouth else [])
        before = rv.visemes.get(prev_v) or rv.visemes.get("X")
        if XFADE_S > 0 and mouth and before and wv < 1.0 and before[0] is not mouth[0]:
            fading = mouth[0].copy()  # only valid when all visemes share one alpha shape; off by default
            fading.putalpha(fading.getchannel("A").point(lambda a: int(a * wv)))
            parts = list(rv.layers) + [("mouth", before[0], before[1], before[2]), ("mouth", fading, mouth[1], mouth[2] + 0.001)]
        mouths = [p for p in parts if p[0] == "mouth"]
        mouth_drop = 0
        if mouths and cur_v not in "XA":
            mouth_drop = rest_lip_drop(rv, mouths[-1], face_w)
        if mouths:  # frame trace for the video QA: how many mouth layers were drawn, and where
            m0 = mouths[-1]
            sprite = layer_image(m0[1])
            bx = sprite.getchannel("A").point(lambda a: 255 if a > 128 else 0).getbbox() or (0, 0, *sprite.size)
            # Mouth follows the head's breathing offset; trace the actual painted position.
            ox, oy = x0 + m0[2][0] + hx, y0 + m0[2][1] + round(breathe) + hy + mouth_drop
            self._mouth_log.append({"char": idx, "v": mouth_tag, "layers": len(mouths), "view": view,
                                    "box": _qa_box((ox + bx[0], oy + bx[1], ox + bx[2], oy + bx[3]))})
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
            dy = breathe * BREATH_SHARE.get(name, 1.0) - (amp * 2 * SS if name == "brows" else 0) + (brow if name == "brows" else 0)
            hdx, hdy = (0, 0) if name in BODY_LAYERS else (hx, hy)
            img = layer_image(img)
            if name == "mouth":
                if head is not None and cur_v not in "XA":
                    img = tuck_mustache_over_mouth(img, layer_image(head[1]), (lx, ly), head[2])
                dy += mouth_drop
            if name == "iris" and socket is not None:
                img, (lx, ly) = self._iris(img, (lx, ly), socket, rv.eye_shift, gx, gy)
            if name in {"brow_left", "brow_right"}:
                side = 0 if name == "brow_left" else 1
                angle = brow_pose[2 + side]
                original = img
                img = img.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True)
                lx -= (img.width - original.width) // 2
                ly -= (img.height - original.height) // 2
                dy += brow_pose[side] * SS
            elif name in {"arm_left", "arm_right"}:
                side = 0 if name == "arm_left" else 1
                angle = arm_pose[side]
                original = img
                img = img.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True)
                lx -= (img.width - original.width) // 2
                ly -= (img.height - original.height) // 2
            stage.alpha_composite(img, (x0 + lx + hdx, y0 + ly + round(dy) + hdy))
        if mouths:  # reference: the one mouth as drawn, measured the way the video QA measures frames
            key = (idx, view, mouth_tag)
            if key not in self._mouth_ref:
                box = self._mouth_log[-1]["box"]
                crop = np.asarray(stage.crop(box).convert("RGB"))
                self._mouth_ref[key] = frameqa.measure(crop, (0, 0, crop.shape[1], crop.shape[0]))
            self._mouth_log[-1]["ref"] = self._mouth_ref[key]

    def _brow(self, idx: int, t: float) -> float:
        """Brow offset for character ``idx``: the emotion of its own current line, eased in and out;
        listeners keep a neutral brow."""
        ms = t * 1000
        for l in self.script:
            if l["speaker"] != idx:
                continue
            a, b = l["startMs"] - EMO_EASE_S * 1000, l["endMs"] + EMO_EASE_S * 1000
            if a <= ms <= b:
                k = min(1.0, (ms - a) / (EMO_EASE_S * 1000), (b - ms) / (EMO_EASE_S * 1000))
                return EMO_BROW.get(str(l.get("emotion") or "neutro"), 0.0) * k
        return 0.0

    def _emotion(self, idx: int, t: float) -> tuple[str, float]:
        """Current emotion and smooth 0..1 transition weight for one speaker."""
        ms = t * 1000
        for line in self.script:
            if line["speaker"] != idx:
                continue
            a, b = line["startMs"] - EMO_EASE_S * 1000, line["endMs"] + EMO_EASE_S * 1000
            if a <= ms <= b:
                weight = min(1.0, (ms - a) / (EMO_EASE_S * 1000), (b - ms) / (EMO_EASE_S * 1000))
                return str(line.get("emotion") or "neutro").lower(), max(0.0, weight)
        return "neutro", 0.0

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
            src = ((cur.get("quote") or {}).get("label") or (cur.get("quote") or {}).get("source", "")) if quote else ""
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
        n = len(self.rigs)
        for idx in range(n):
            img, xy = self._set_piece("shadow", idx, False)
            stage.alpha_composite(img, xy)
        order = sorted(range(n), key=lambda i: 1 if (line and line["speaker"] == i) else 0)  # speaker in front
        single = shot is not None and shot.kind in ("ots", "cu", "single") and n > 1
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

    def _focus(self, shot: Shot, drift: tuple[float, float, float], t: float | None = None) -> tuple[float, float]:
        """Output px the vignette and key glow centre on: the face nearest the camera in singles and
        close-ups; in wide and two shots of a debate the light leans toward whoever has the floor
        (``SPEAKER_PULL`` of the way from the middle, eased between lines); the middle otherwise."""
        if shot.kind in ("wide", "two", "board", ""):
            cx, cy = self.OW / 2, self.OH * 0.46
            pull = self._floor(t) if t is not None and shot.kind != "board" and len(self.places) > 1 else 0.0
            if pull:
                faces = [self._to_output((W * p[0] * SS, 0, W * p[0] * SS, 0), shot, drift)[0] for p in self.places[:2]]
                side = faces[1] if pull > 0 else faces[0]
                cx += SPEAKER_PULL * abs(pull) * (float(side) - cx)
            return cx, cy
        px = min((p[0] for p in self.places), key=lambda p: abs(W * p - shot.cx))
        x, y = W * px * SS, H * FACE_Y * SS
        box = self._to_output((x, y, x, y), shot, drift)
        return float(box[0]), float(box[1])

    def _floor(self, t: float) -> float:
        """Who has the floor, -1 (left speaker) .. +1 (right), eased over ``FLOOR_EASE_S`` from each
        line's start; 0 under the titles."""
        if t < self.title_end or (self.end_start is not None and t >= self.end_start):
            return 0.0
        level = 0.0
        for line in self.script:
            start = line["startMs"] / 1000 - 0.15
            if start > t:
                break
            target = -1.0 if line["speaker"] == 0 else 1.0
            level += (target - level) * look.ease((t - start) / FLOOR_EASE_S)
        return level

    def _shoot(self, stage: Image.Image, shot: Shot, drift: tuple[float, float, float], blur: float) -> Image.Image:
        size = (self.OW, self.OH)

        def aff(depth: str, scale: float = 1.0) -> tuple[float, ...]:
            return self._aff(shot, depth, drift, scale)

        lo, hi, w = look.dof_level(shot.zoom, len(self.far_dof))

        def plate(stack: list[Image.Image], depth: str) -> Image.Image:
            img = stack[lo].transform(size, Image.AFFINE, aff(depth), Image.BILINEAR)
            if hi != lo and w > 0.02:  # between two lens settings: blend them (continuous focus pull)
                img = Image.blend(img, stack[hi].transform(size, Image.AFFINE, aff(depth), Image.BILINEAR), w)
            return img

        out = plate(self.far_dof, "far").convert("RGBA")
        out.alpha_composite(plate(self.mid_dof, "mid"))
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
            # soft dark plate behind the block, so white text stays legible over a white beard or a lit set
            tw = max([d.textlength(r, font=font) for r in rows] + [lw])
            pad_x, top = round(30 * self.k), y0 - round((34 if label else 12) * self.k)
            plate = Image.new("RGBA", img.size, (0, 0, 0, 0))
            ImageDraw.Draw(plate).rounded_rectangle(
                (self.OW / 2 - tw / 2 - pad_x, top, self.OW / 2 + tw / 2 + pad_x, y0 + len(rows) * round(44 * self.k) + round(4 * self.k)),
                round(22 * self.k), fill=(6, 6, 9, CAPTION_PLATE_A))
            plate = plate.filter(ImageFilter.GaussianBlur(round(14 * self.k)))
            plate.alpha_composite(shadow)
            shadow = plate
            d = ImageDraw.Draw(shadow)
            if label:
                _tracked(d, (self.OW / 2 - lw / 2, y0 - round(26 * self.k)), label, self.speaker_font, (*self.accent, 255), 2.6)
            for i, s in enumerate(rows):
                d.text((self.OW / 2, y0 + i * round(44 * self.k)), s, font=font, fill=(255, 255, 255, 255), anchor="mt")
            self._cache[key] = shadow
        return self._cache[key]

    def _tag(self, line: dict[str, Any]) -> Image.Image | None:
        """Top-center chip: CITAÇÃO + source for a real quote, PARÁFRASE for written lines."""
        if self.lang not in languages.PROFILES:
            return None
        kind = line.get("kind")
        if kind not in ("quote", "paraphrase"):
            return None
        ref = line.get("quote") or line.get("basis") or {}
        key = ("tag", kind, ref.get("label") or ref.get("source", ""))
        if key not in self._cache:
            pt = self.lang == "pt-BR"
            if kind == "quote":
                head = "CITAÇÃO REAL" if pt else "REAL QUOTE"
                src = f"{line['quote'].get('label') or line['quote'].get('source') or ''} · Wikiquote".lstrip(" ·")
            else:
                head = "PARÁFRASE" if pt else "PARAPHRASE"
                basis = (line.get("basis") or {}).get("label") or (line.get("basis") or {}).get("source")
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

    def _role_rows(self, role: str, max_w: float) -> tuple[Any, list[str]]:
        """Wrap and shrink the topic line so a long title is not cut to 'PERSPEC'."""
        probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        text = " ".join((role or "").upper().split())
        chosen_font, chosen = self.role_font, _wrap(probe, text, self.role_font, max_w) or [""]
        for size in (15, 13, 12, 11, 10):
            font = _font("SemiBold", size)
            rows = _wrap(probe, text, font, max_w)
            if rows and len(rows) <= 2 and all(probe.textlength(row, font=font) <= max_w + 1 for row in rows):
                return font, rows
            chosen_font, chosen = font, rows
        return chosen_font, chosen[:3] or [""]

    def _lower_third(self, idx: int) -> Image.Image:
        key = ("lt", idx)
        if key not in self._cache:
            name = pretty_name(self.names[idx]) if idx < len(self.names) else ""
            role = self.roles[idx] if idx < len(self.roles) else ""
            max_w = 520
            role_font, rows = self._role_rows(role, max_w)
            img = Image.new("RGBA", (620, 78 + 22 * max(1, len(rows))), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            nw = d.textlength(name, font=self.name_font)
            rw = max((d.textlength(row, font=role_font) for row in rows), default=0)
            pw = int(min(620, max(nw, rw) + 52))
            ph = img.height - 4
            panel = Image.new("RGBA", img.size, (0, 0, 0, 0))
            ImageDraw.Draw(panel).rounded_rectangle((0, 0, pw, ph), 10, fill=(8, 8, 10, 170))
            img.alpha_composite(panel)
            d.rounded_rectangle((0, 0, 6, ph), 3, fill=(*self.accent, 255))
            d.text((26, 12), name, font=self.name_font, fill=(255, 255, 255, 255))
            for i, row in enumerate(rows):
                d.text((27, 48 + i * (role_font.size + 3)), row, font=role_font, fill=(*self.accent, 235))
            self._cache[key] = self._big(img.crop((0, 0, pw + 1, ph + 1)))
        return self._cache[key]

    def lower_third_y(self, shot: Shot | None, card_h: int) -> int:
        """Name card in its own band, above the subtitle strip.

        The card used to share the caption band, so the first lower-third covered
        the first subtitle. ``cap_y`` is the top of the captions.
        """
        gap = round(12 * self.k)
        return max(0, self.cap_y - card_h - gap)

    def _overlays(self, frame: Image.Image, t: float, line: dict[str, Any] | None, shot: Shot | None = None) -> None:
        frame.alpha_composite(self.scrim)
        self._chapter(frame, t)
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
                    # wide/two: under the speaker; close shots: on the open side, below the chin
                    close = shot is not None and shot.kind in ("ots", "cu", "mcu", "single")
                    right = self.template == "debate" and (line["speaker"] == 1) != close
                    slide = round(30 * (1 - a)) * (1 if right else -1)
                    x = (self.OW - 64 - lt.width if right else 64) + slide
                    frame.alpha_composite(_fade(lt, a), (x, self.lower_third_y(shot, lt.height)))
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
        self._face_log = []
        stage = self._stage(t, line, amp, shot)
        drift = look.handheld(t)
        self.trace.append({"t": round(t, 3), "takes": len(cam), "blur": round(max(c[2] for c in cam), 2),
                           "mouths": [m | {"box": self._to_output(m["box"], shot, drift)} for m in self._mouth_log],
                           "faces": self._face_log})
        out = None
        for weight, shot, blur in cam:
            img = self._shoot(stage, shot, drift, blur)
            out = img if out is None else Image.blend(out, img, weight)
        rgb = out.convert("RGB").point(self.lut)
        if abs(self.sat - 1.0) > 1e-3:
            rgb = ImageEnhance.Color(rgb).enhance(self.sat)
        rgb = look.bloom(rgb, self.bloom)
        focus = self._focus(cam[-1][1], drift, t)
        rgb = ImageChops.screen(rgb, look.vignette_at(self.glow_field, focus))
        rgb = ImageChops.multiply(rgb, look.vignette_at(self.vig_field, focus))
        if self.texture is not None:
            rgb = rgb.convert("RGBA")
            rgb.alpha_composite(self.texture)
            rgb = rgb.convert("RGB")
        rgb = self._titles(rgb, t)
        fade_in = FADE_IN_FLOOR + (1.0 - FADE_IN_FLOOR) * look.ease(t / FADE_IN_S)
        fade = fade_in * look.ease((self.duration_s - t) / FADE_OUT_S) * titles.dip(t, self.dips)
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
