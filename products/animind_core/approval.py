"""Cinema approval pack for a stick figure and an illustrated face.

No image API. Mouths are the Rhubarb set, seated on the nose–chin anchor.
Brows come from ``browModel``. Eyes squint and look with the line. A short
monologue uses the envelope viseme path, the same fallback as a clip with
no Rhubarb binary.
"""
from __future__ import annotations

import json
import os
import wave
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from . import (
    brow_draw,
    eyes,
    human_eyes,
    mouth_anchor,
    mouth_fit,
    mouthset,
    qa,
    render,
    rig,
    scene,
    sheet,
    stick,
)

PAID_KEYS = (
    "GEMINI_API_KEY", "GOOGLE_API_KEY", "TOGETHER_API_KEY",
    "OPENROUTER_API_KEY", "ANIMIND_IMAGE_MODEL",
)
CANVAS = (640, 800)
FACE = [190, 78, 450, 372]
LEFT_EYE = [246, 184, 310, 236]
RIGHT_EYE = [330, 184, 394, 236]
BEATS = (
    (0.20, 2.50, "neutro", "I kept still and looked straight ahead."),
    (2.75, 5.05, "mau", "Then the thought turned sharp and I narrowed my eyes."),
    (5.30, 7.60, "alegre", "A better idea arrived and the whole face lifted."),
    (7.85, 10.15, "triste", "It faded, and I looked down at what was left."),
)
MOOD_SAMPLES = ("neutral", "evil", "happy", "sad", "perplexed")


def clear_paid_keys() -> None:
    """The approval pack must not call a paid image model."""
    for key in PAID_KEYS:
        os.environ.pop(key, None)


def legalize_mouths(skin: Path) -> dict[str, Any]:
    """Seat and open a skin that was not built by the product generators.

    ``stick.build`` and :func:`build_illustrated` already call :func:`mouth_fit.apply`.
    """
    return mouth_fit.apply(skin)


def build_stick(dest: Path) -> Path:
    """Procedural stick hero. ``stick.build`` seats and opens the mouths."""
    dest.mkdir(parents=True, exist_ok=True)
    stick.build(dest, lambda path: str(path), preset="teacher")
    return dest


def _brow_poly(x0: int, x1: int, y: int, thick: float = 11.0) -> list[tuple[int, int]]:
    upper, lower = [], []
    span = max(1, x1 - x0)
    for index, x in enumerate(range(x0, x1)):
        t = index / span
        height = 2.5 + thick * (1 - (2 * t - 1) ** 2)
        upper.append((x, int(y - height / 2)))
        lower.append((x, int(y + height / 2)))
    return upper + lower[::-1]


def _eye_geom(center: tuple[int, int]) -> dict[str, Any]:
    upper, lower = human_eyes._almond(center, 11)
    return {"upper": upper, "lower": lower, "iris": center, "radius": 8}


def build_illustrated(dest: Path) -> Path:
    """A drawn portrait cut into human eyes and vector brows. No image API."""
    dest.mkdir(parents=True, exist_ok=True)
    plate = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
    draw = ImageDraw.Draw(plate)
    skin_tone = (232, 186, 156, 255)
    ink = (48, 36, 32, 255)
    draw.ellipse(tuple(FACE), fill=skin_tone, outline=ink, width=4)
    left = _eye_geom((278, 210))
    right = _eye_geom((362, 210))
    brows = [_brow_poly(236, 314, 168), _brow_poly(326, 404, 168)]
    for eye in (left, right):
        draw.polygon(eye["upper"] + eye["lower"][::-1], fill=(248, 246, 242, 255))
        cx, cy = eye["iris"]
        r = eye["radius"]
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(36, 28, 24, 255))
    for poly in brows:
        draw.polygon(poly, fill=(42, 30, 24, 255))
    built = human_eyes.build(plate, eyes_geom=[left, right], brows=brows)
    human_eyes.save(dest, built)
    plate.save(dest / "character.png", "PNG")
    colours = {
        "outline": ink, "cavity": (42, 30, 34, 255), "teeth": (247, 244, 236, 255),
        "tongue": (190, 86, 92, 255), "lip": (158, 78, 76, 255),
    }
    mouths = mouthset.draw_set(78, colours, open_cap=34)
    expressions = mouthset.draw_expressions(78, colours, open_cap=26)
    _write_illustrated_views(dest, built, mouths, expressions)
    mouth_fit.apply(dest)
    return dest


def _write_layer(folder: Path, name: str, image: Image.Image, xy: tuple[int, int], z: int) -> dict[str, Any]:
    folder.mkdir(parents=True, exist_ok=True)
    image.save(folder / f"{name}.png", "PNG")
    return {"name": name, "url": f"{name}.png", "x": xy[0], "y": xy[1], "w": image.width, "h": image.height, "z": z}


def _mirror_canvas(image: Image.Image) -> Image.Image:
    return image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)


def _write_illustrated_views(
    dest: Path,
    built: dict[str, Any],
    mouths: dict[str, Image.Image],
    expressions: dict[str, Image.Image],
) -> None:
    body = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
    ImageDraw.Draw(body).rounded_rectangle((150, 360, 490, 760), 40, fill=(72, 96, 140, 255))
    order = (
        ("body", body, 0),
        ("head", built["head"], 2),
        ("eye_white", built["eye_white"], 3),
        ("iris", built["iris"], 4),
        ("eye_socket", built["eye_socket"], 4),
        ("eyes_half", built["eyes_half"], 5),
        ("eyes_closed", built["eyes_closed"], 6),
        ("brow_left", built["brow_left"], 7),
        ("brow_right", built["brow_right"], 7),
    )
    model = built["browModel"]
    anchor = mouth_anchor.place(built["head"], FACE, LEFT_EYE, RIGHT_EYE, view="front")
    mouth_box = list(anchor.as_box(FACE, 90))
    meta = {
        "type": "illustrated", "size": list(CANVAS),
        "boxes": {"face": FACE, "left_eye": LEFT_EYE, "right_eye": RIGHT_EYE, "mouth": mouth_box},
        "mouthBox": mouth_box,
    }
    (dest / "rig.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    (dest / "rig_front.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    seated_mouths = mouth_anchor.seat(mouths, anchor)
    seated_expressions = mouth_anchor.seat(expressions, anchor)
    for name, mirror in (("front", False), ("left", False), ("right", True)):
        folder = dest / "views" / name
        parts = []
        for layer_name, image, z in order:
            sprite = _mirror_canvas(image) if mirror else image
            parts.append(_write_layer(folder, layer_name, sprite, (0, 0), z))
        view_anchor = mouth_anchor.place(
            _mirror_canvas(built["head"]) if mirror else built["head"],
            FACE, LEFT_EYE, RIGHT_EYE, view=name,
        )
        mouth_sprites = {
            code: (_mirror_canvas(image) if mirror else image) for code, image in mouths.items()
        }
        expression_sprites = {
            code: (_mirror_canvas(image) if mirror else image) for code, image in expressions.items()
        }
        visemes = {}
        for code, (image, xy) in mouth_anchor.seat(mouth_sprites, view_anchor).items():
            visemes[code] = _write_layer(folder / "mouths", f"mouth_{code}", image, xy, 10)
            visemes[code]["url"] = f"mouths/{visemes[code]['url']}"
        expressed = {}
        for code, (image, xy) in mouth_anchor.seat(expression_sprites, view_anchor).items():
            expressed[code] = _write_layer(folder / "expressions", f"expression_{code}", image, xy, 10)
            expressed[code]["url"] = f"expressions/{expressed[code]['url']}"
        irises = [[362, 210, 8], [278, 210, 8]] if mirror else [[278, 210, 8], [362, 210, 8]]
        view = {
            "canvas": {"w": CANVAS[0], "h": CANVAS[1]},
            "layers": parts,
            "visemes": visemes,
            "expressions": expressed,
            "eyeRig": {"maxShift": [10, 6], "irises": irises},
            "browModel": brow_draw.mirror_model(model, CANVAS[0]) if mirror else model,
        }
        (folder / "view.json").write_text(json.dumps(view, indent=2), encoding="utf-8")
    # mouth_fit.apply reseats this first pass onto the nose–chin anchor.
    del seated_mouths, seated_expressions


def approval_gates(skin: Path) -> dict[str, Any]:
    gates = qa.run(skin)
    return {key: {"pass": bool((gates.get(key) or {}).get("pass")), "note": (gates.get(key) or {}).get("note"),
                  "bad": (gates.get(key) or {}).get("bad"), "value": (gates.get(key) or {}).get("value")}
            for key in qa.APPROVAL}


def mood_frame(skin: Path, state: str, *, vis: str = "C") -> Image.Image:
    """Front character. Brows and eyes follow ``state``. The mouth stays on ``vis``."""
    view = rig.load_view(skin, "front")
    return sheet.compose(
        view, vis=vis, lids=brow_draw.eye_lids(state), brow_state=state,
        gaze=brow_draw.gaze_bias(state), brow_pose=sheet._FALLBACK_POSE.get(state, (0, 0, 0, 0)),
    )


def face_zoom(skin: Path, state: str, *, scale: int = 2) -> Image.Image:
    frame = mood_frame(skin, state)
    box = sheet._face_box(skin, "front", frame.size)
    crop = frame.crop(box)
    return crop.resize((crop.width * scale, crop.height * scale), Image.Resampling.LANCZOS)


def contact_sheet(skin: Path) -> Path:
    return sheet.build(skin, name=skin.name)


def open_time(start: float, end: float, seed: int = 7) -> float:
    """A moment in the beat when the blink is open, so the mood squint is what the eye shows."""
    step = 1 / 24
    t = start + 0.2
    while t < end - 0.15:
        if eyes.blink(t, seed) == "open":
            return t
        t += step
    return (start + end) / 2


def _pcm(seconds: float, syllables_hz: float = 3.5) -> np.ndarray:
    count = max(1, int(seconds * render.SAMPLE_RATE))
    t = np.arange(count) / render.SAMPLE_RATE
    gate = np.clip(np.sin(2 * np.pi * syllables_hz * t), 0, None) ** 2
    return (np.sin(2 * np.pi * 170 * t) * gate * 18000).astype(np.int16)


def _write_wav(path: Path, pcm: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(render.SAMPLE_RATE)
        handle.writeframes(pcm.astype(np.int16).tobytes())


def monologue(skin: Path, *, viseme: str | None = None) -> tuple[scene.Scene, np.ndarray]:
    """About ten seconds. Four moods. ``viseme`` holds one mouth; otherwise the envelope talks."""
    lines = []
    parts: list[np.ndarray] = []
    cursor = 0.0
    for start, end, emotion, text in BEATS:
        if start > cursor:
            parts.append(np.zeros(int((start - cursor) * render.SAMPLE_RATE), np.int16))
        clip = _pcm(end - start)
        parts.append(clip)
        if viseme:
            cues = [{"t": round(start, 3), "v": viseme}]
        else:
            raw = render.envelope_visemes(clip)
            cues = render.smooth_visemes([{"t": round(start + float(t), 3), "v": str(v)} for t, v in raw])
        lines.append({
            "speaker": 0, "text": text, "kind": "paraphrase", "beat": "argumento",
            "emotion": emotion, "startMs": int(start * 1000), "endMs": int(end * 1000),
            "view": "front", "visemes": cues,
        })
        cursor = end
    duration = BEATS[-1][1] + 0.35
    if cursor < duration:
        parts.append(np.zeros(int((duration - cursor) * render.SAMPLE_RATE), np.int16))
    track = np.concatenate(parts) if parts else np.zeros(1, np.int16)
    views = {}
    for name in ("front", "left", "right"):
        data = rig.load_view(skin, name)
        views[name] = scene.load_rig_view(
            data, scene.fit_scale(data["canvas"]["h"], "solo", 0), scene.light("studio", "solo", 0),
        )
    built = scene.Scene(
        template="solo", scenario="studio", rigs=[views], script=lines,
        envelope=render.envelope(track), env_fps=render.ENV_FPS, duration_s=duration,
        disclaimer="Parody", names=["Nia"], roles=["A short monologue"], lang="en-US",
        fmt="16:9", style="cartoon", topic="a change of mind",
    )
    return built, track


def render_monologue(skin: Path, dest: Path, *, fps: int = 24) -> Path:
    """Write the voiced monologue. Audio is the syllable envelope, not a paid voice."""
    clear_paid_keys()
    built, track = monologue(skin)
    dest.parent.mkdir(parents=True, exist_ok=True)
    wav = dest.with_suffix(".wav")
    _write_wav(wav, track)
    from animation.parametric.renderer import AnimationRenderer  # noqa: PLC0415

    AnimationRenderer(width=built.OW, height=built.OH, fps=fps).render(
        frame_iter=built.frames(),
        audio_path=wav,
        output_path=dest,
        duration_s=built.duration_s,
        preset="veryfast",
        crf=22,
        tune=None,
    )
    wav.unlink(missing_ok=True)
    return dest


def write_pack(dest: Path, *, video: bool = True, fps: int = 24) -> dict[str, Any]:
    """Sheets, 2× face zooms, and the monologue. Nothing here is committed."""
    clear_paid_keys()
    dest.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"paidKeys": "unset", "files": []}
    for kind, builder in (("stick", build_stick), ("illustrated", build_illustrated)):
        skin = dest / "_build" / kind
        builder(skin)
        report[kind] = {"gates": approval_gates(skin)}
        sheet_path = contact_sheet(skin)
        copied = dest / f"{kind}_review_sheet.png"
        copied.write_bytes(sheet_path.read_bytes())
        report["files"].append(str(copied))
        for state in ("neutral", "evil", "happy"):
            zoom = dest / f"{kind}_zoom_{state}.png"
            face_zoom(skin, state).save(zoom, "PNG")
            report["files"].append(str(zoom))
        if kind == "illustrated":
            still = human_eyes.render_still_sheet(skin, dest / "illustrated_emotion_sheet.png")
            report["files"].append(str(still))
        if video:
            movie = render_monologue(skin, dest / f"{kind}_monologue.mp4", fps=fps)
            report["files"].append(str(movie))
        print(kind, json.dumps(report[kind]["gates"]))
    (dest / "gates.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
