"""Debate test renderer: character mouths, male voices, and contraplano cuts.

A contraplano pass uses the approved three-quarter views. ChatGPT stands on
the left looking right and opens the exchange. Claude stands on the right
looking left. The shot-reverse-shot director hard-cuts to whoever is speaking.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
import shutil
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

from utils.pipeline_paths import assets_root, outputs_root

from .render.facial_rig import (
    export_pilot_facial_sheet,
    face_layout,
    generate_character_acting,
    install_view_anchors,
)
from .render.visemes import (
    LLAMA_CHIN_WIDTH,
    LLAMA_WIDE_MOUTH,
    PALETTES,
    MechaPalette,
    draw_mouths,
    generate_viseme_set,
    metal_stroke,
    viseme_file,
)
from .voice import assign_debater_voices
from .puppet import REST_MOUTH_STATES, rest_mouth_layer_key
from .types import VISEMES

_LOG = logging.getLogger("animator.pipeline")

_SOURCE_ALIASES = {"llama": "llama_cyborg_v2"}
_HARNESS = (
    Path(r"G:\My Drive\Z sosFiles\Z_act\@ NETWORK\@MEDIAUPSCALE_FACTORY_DYNAMIC_CONTENT")
    / "Unified Multi-Page Factory"
    / "outputs"
    / "aiwake"
    / "_test_harness"
)
_TEST_OUTPUT = _HARNESS / "chatgpt_vs_claude_mouth_test.mp4"
_CONTRAPLANO_OUTPUT = _HARNESS / "chatgpt_vs_claude_contraplano_test.mp4"
_PILOT_SHEET = _HARNESS / "chatgpt_pilot_facial_inspection.png"
_PILOT_VIDEO = _HARNESS / "chatgpt_pilot_acting_test.mp4"
_CAST = {
    "chatgpt_cyborg_v1": {"label": "CHATGPT", "accent": "#D4A466", "text": "A clear answer beats a clever one."},
    "claude_cyborg_v1": {"label": "CLAUDE", "accent": "#B05434", "text": "Only when that answer is also true."},
}


def resolve_puppet(name: str) -> str:
    """Map the blueprint nickname ``llama`` onto the approved skin folder."""
    token = name.strip()
    return _SOURCE_ALIASES.get(token, token)


def clone_mouths(source: str, puppets: list[str]) -> None:
    """Regenerate visemes and acting mouths from the Llama patch."""
    blueprint = resolve_puppet(source)
    blueprint_dir = assets_root() / "puppets" / blueprint
    if not blueprint_dir.is_dir():
        raise FileNotFoundError(f"blueprint puppet is missing: {blueprint_dir}")
    _LOG.info("cloning mouth geometry from %s", blueprint)
    for character_id in puppets:
        if character_id not in PALETTES:
            raise SystemExit(f"no Llama mouth palette for {character_id}")
        generate_viseme_set(character_id)
        generate_character_acting(character_id)


def _hex(color: tuple[int, int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(color[0], color[1], color[2])


def _shift(points: tuple[int, int], origin: tuple[int, int]) -> tuple[int, int]:
    return points[0] + origin[0], points[1] + origin[1]


def _opaque_width(image: Image.Image) -> int:
    alpha = np.asarray(image)[..., 3]
    xs = np.nonzero(alpha > 8)[1]
    if xs.size == 0:
        return image.width
    return int(xs.max() - xs.min() + 1)


def _paste_layer(canvas_size: tuple[int, int], image: Image.Image, origin: tuple[int, int]) -> Image.Image:
    """Paste ``image`` at ``origin``, clipping anything that falls outside the stage."""
    layer = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    x, y = origin
    src_x = max(0, -x)
    src_y = max(0, -y)
    dest_x = max(0, x)
    dest_y = max(0, y)
    width = min(image.width - src_x, canvas_size[0] - dest_x)
    height = min(image.height - src_y, canvas_size[1] - dest_y)
    if width <= 0 or height <= 0:
        return layer
    cropped = image.crop((src_x, src_y, src_x + width, src_y + height))
    layer.alpha_composite(cropped, (dest_x, dest_y))
    return layer


def _bind_to_head(head_size: tuple[int, int], child: Image.Image) -> Image.Image:
    """Stage 1. A facial child lives in the head sprite's own pixels."""
    plate = Image.new("RGBA", head_size, (0, 0, 0, 0))
    plate.alpha_composite(child, (0, 0))
    return plate


def _promote_head(
    canvas_size: tuple[int, int],
    head_plate: Image.Image,
    head_xy: tuple[int, int],
) -> Image.Image:
    """Stage 2. The fused head is parented once. Children are not moved again."""
    return _paste_layer(canvas_size, head_plate, head_xy)


def _mouth_layer(
    canvas_size: tuple[int, int],
    sprite: Image.Image,
    anchor: tuple[int, int],
    scale: float,
    rot_deg: float = 0.0,
) -> Image.Image:
    resized = sprite.resize(
        (max(1, int(round(sprite.width * scale))), max(1, int(round(sprite.height * scale)))),
        Image.Resampling.LANCZOS,
    )
    if abs(rot_deg) > 0.05:
        resized = resized.rotate(-rot_deg, resample=Image.Resampling.BICUBIC, expand=True)
    layer = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    layer.alpha_composite(resized, (anchor[0] - resized.width // 2, anchor[1] - resized.height // 2))
    return layer


def _mouth_on_head(
    head_size: tuple[int, int],
    sprite: Image.Image,
    local_anchor: tuple[int, int],
    scale: float,
    rot_deg: float = 0.0,
) -> Image.Image:
    """Dock a mouth in head-local pixels, then let the head carry it."""
    return _mouth_layer(head_size, sprite, local_anchor, scale, rot_deg)


def _shutter_layer(
    canvas_size: tuple[int, int],
    optics: list[tuple[float, float, float]],
    fill: tuple[int, int, int, int],
    *,
    closed: bool,
) -> Image.Image:
    layer = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer, "RGBA")
    for center_x, center_y, radius in optics:
        box = (
            center_x - radius,
            center_y - radius,
            center_x + radius,
            center_y + radius,
        )
        if closed:
            draw.ellipse(box, fill=fill)
        else:
            draw.pieslice(box, 180, 360, fill=fill)
    return layer


def build_render_skin(
    character_id: str,
    destination: Path,
    *,
    view_name: str = "facing_front",
    contraplano: bool = False,
) -> Path:
    """Assemble one view canvas the shot-reverse-shot rig can load.

    Contraplano uses the three-quarter view and leaves framing to the
    director's lead anchor, so a right-facing hero sits on the left and a
    left-facing hero sits on the right.
    """
    root = assets_root() / "puppets" / character_id
    manifest = json.loads((root / "puppet.json").read_text(encoding="utf-8"))
    view = manifest["views"][view_name]
    head = Image.open(root / view["head"]).convert("RGBA")
    body = Image.open(root / view["body"]).convert("RGBA")
    head_xy = (int(view["head_xy"][0]), int(view["head_xy"][1]))
    body_xy = (int(view["body_xy"][0]), int(view["body_xy"][1]))
    # Locked views keep the approved head/body gap. The plate is the
    # union of those coordinates, and the camera fits that one piece.
    locked_plate = str(view.get("status") or "") == "locked_approved"
    min_x = min(0, head_xy[0], body_xy[0])
    min_y = min(0, head_xy[1], body_xy[1])
    head_xy = (head_xy[0] - min_x, head_xy[1] - min_y)
    body_xy = (body_xy[0] - min_x, body_xy[1] - min_y)
    canvas_size = (
        max(head_xy[0] + head.width, body_xy[0] + body.width),
        max(head_xy[1] + head.height, body_xy[1] + body.height),
    )
    layout = face_layout(head)
    facial_rig = view.get("facial_rig") or {}
    rig_mouth = facial_rig.get("mouth") or {}
    mouth_spec = rig_mouth or view.get("mouth") or {}
    if mouth_spec:
        mouth_center = mouth_spec.get("center")
        if mouth_center is None:
            mouth_center = (mouth_spec["x"], mouth_spec["y"])
        local_mouth = (int(mouth_center[0]), int(mouth_center[1]))
        mouth_anchor = _shift(local_mouth, head_xy)
        mouth_rot = float(mouth_spec.get("rot_deg") or 0.0)
        mouth_mul = float(mouth_spec.get("scale") or 1.0)
    else:
        local_mouth = (int(layout.chin_anchor[0]), int(layout.chin_anchor[1]))
        mouth_anchor = _shift(local_mouth, head_xy)
        mouth_rot = 0.0
        mouth_mul = 1.0
    palette = PALETTES.get(
        character_id,
        MechaPalette(character_id, (196, 154, 78, 255)),
    )
    stroke = metal_stroke(palette.bezel)
    native_dir = (
        root / rig_mouth["asset_dir"]
        if rig_mouth.get("asset_dir")
        else None
    )
    mouth_file = (
        (lambda code: native_dir / f"mouth_{code}.png")
        if native_dir is not None
        else (lambda code: viseme_file(root, code))
    )
    wide = Image.open(mouth_file("C")).convert("RGBA")
    if rig_mouth.get("target_width"):
        mouth_scale = float(rig_mouth["target_width"]) / wide.width
    else:
        mouth_scale = (
            layout.chin_width
            * (LLAMA_WIDE_MOUTH / LLAMA_CHIN_WIDTH)
            * mouth_mul
        ) / _opaque_width(wide)
    skin = destination / character_id
    mouths = skin / "mouths"
    mouths.mkdir(parents=True, exist_ok=True)
    _paste_layer(canvas_size, body, body_xy).save(skin / "body.png", compress_level=1)
    _promote_head(canvas_size, head, head_xy).save(skin / "head.png", compress_level=1)
    Image.new("RGBA", canvas_size, (0, 0, 0, 0)).save(skin / "eyes_open.png", compress_level=1)
    optics = []
    for optic in (layout.left, layout.right):
        center = _shift(optic.center, head_xy)
        optics.append((float(center[0]), float(center[1]), float(optic.radius)))
    lids = facial_rig.get("eyes_lids") or {}
    half_asset = root / lids["half_asset"] if lids.get("half_asset") else None
    full_asset = root / lids["full_asset"] if lids.get("full_asset") else None
    if half_asset is not None and half_asset.is_file():
        with Image.open(half_asset) as opened:
            half_lid = opened.convert("RGBA")
        _promote_head(
            canvas_size,
            _bind_to_head(head.size, half_lid),
            head_xy,
        ).save(skin / "eyes_half.png", compress_level=1)
    else:
        lid = (stroke[0] // 2, stroke[1] // 2, stroke[2] // 2, 255)
        _shutter_layer(canvas_size, optics, lid, closed=False).save(
            skin / "eyes_half.png",
            compress_level=1,
        )
    if full_asset is not None and full_asset.is_file():
        with Image.open(full_asset) as opened:
            full_lid = opened.convert("RGBA")
        _promote_head(
            canvas_size,
            _bind_to_head(head.size, full_lid),
            head_xy,
        ).save(skin / "eyes_blink.png", compress_level=1)
    else:
        lid = (stroke[0] // 2, stroke[1] // 2, stroke[2] // 2, 255)
        _shutter_layer(canvas_size, optics, lid, closed=True).save(
            skin / "eyes_blink.png",
            compress_level=1,
        )
    glow = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((4, 4, 28, 28), fill=(*palette.bezel[:3], 40))
    glow.save(skin / "glow.png", compress_level=1)
    Image.new("RGB", (64, 64), (12, 14, 18)).save(skin / "bg.png", compress_level=1)

    sprites = {
        code: Image.open(mouth_file(code)).convert("RGBA")
        for code in VISEMES
    }
    expression_dir = native_dir or (root / "mouths" / "expressions")
    expressions = {
        "neutral": sprites["X"],
        "smug_smile": Image.open(expression_dir / "mouth_smug.png").convert("RGBA"),
        "stressed_grimace": Image.open(expression_dir / "mouth_angry.png").convert("RGBA"),
    }
    for code, sprite in sprites.items():
        _promote_head(
            canvas_size,
            _mouth_on_head(head.size, sprite, local_mouth, mouth_scale, mouth_rot),
            head_xy,
        ).save(mouths / f"mouth_{code}.png", compress_level=1)
    for state in REST_MOUTH_STATES:
        _promote_head(
            canvas_size,
            _mouth_on_head(head.size, expressions[state], local_mouth, mouth_scale, mouth_rot),
            head_xy,
        ).save(mouths / f"{rest_mouth_layer_key(state)}.png", compress_level=1)

    head_alpha = np.asarray(head)[..., 3]
    rows = np.nonzero(head_alpha > 8)[0]
    head_bottom = head_xy[1] + (int(rows.max()) + 1 if rows.size else head.height)
    head_center_x = head_xy[0] + head.width // 2
    eye_boxes = []
    for center_x, center_y, radius in optics:
        eye_boxes.append(
            [
                int(round(center_x - radius)),
                int(round(center_y - radius)),
                int(round(center_x + radius)),
                int(round(center_y + radius)),
            ]
        )
    payload = {
        "character_id": character_id,
        "skin_version": "v2",
        "canvas_size": list(canvas_size),
        "mouth_style": "ghibli_mecha",
        "eye_style": "circular",
        "anchors": {
            "head_pivot": [head_center_x, int(round((optics[0][1] + optics[1][1]) / 2))],
            "neck_pivot": [head_center_x, head_bottom],
            "mouth": [head_center_x, head_bottom],
            "left_eye": [int(round(optics[0][0])), int(round(optics[0][1]))],
            "right_eye": [int(round(optics[1][0])), int(round(optics[1][1]))],
            "eye_radius": int(round((optics[0][2] + optics[1][2]) / 2)),
        },
        "theme": {"glow_color": _hex(palette.bezel), "glow_radius": 24},
        "palette": {"ink_outline": _hex(stroke)},
        "brows": _skin_brow_payload(
            facial_rig,
            head_xy,
            layout.nameplate_bottom,
            int(round(max(optic.radius for optic in (layout.left, layout.right)) * 2.3)),
        ),
        "framing": (
            {"locked_plate": True}
            if locked_plate
            else ({} if contraplano else {"bottom_anchor": True})
        ),
        "calibration": {
            "eye_bboxes": eye_boxes,
            "lens_circles": [[center_x, center_y, radius] for center_x, center_y, radius in optics],
        },
        "layers": {
            "body": "body.png",
            "head": "head.png",
            "eyes_open": "eyes_open.png",
            "eyes_half": "eyes_half.png",
            "eyes_blink": "eyes_blink.png",
            "glow": "glow.png",
            "bg": "bg.png",
            **{f"mouth_{code}": f"mouths/mouth_{code}.png" for code in VISEMES},
            **{rest_mouth_layer_key(state): f"mouths/{rest_mouth_layer_key(state)}.png" for state in REST_MOUTH_STATES},
        },
    }
    (skin / "puppet.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    _LOG.info(
        "%s canvas=%s mouth=%s chin_w=%s plate_bottom=%s",
        character_id,
        canvas_size,
        mouth_anchor,
        layout.chin_width,
        layout.nameplate_bottom,
    )
    _LOG.info("%s view=%s contraplano=%s", character_id, view_name, contraplano)
    return skin


def _skin_brow_payload(
    facial_rig: dict,
    head_xy: tuple[int, int],
    nameplate_bottom: int,
    width_px: int,
) -> dict:
    """Copy the analyzer brow matrix into canvas space without a second scale."""
    source = facial_rig.get("eyebrows") or {}
    if source.get("left") and source.get("right"):
        nodes = {"style": str(source.get("style") or "ink")}
        for side in ("left", "right"):
            node = dict(source[side])
            center = node.get("center") or [0, 0]
            node["center"] = [
                int(center[0]) + head_xy[0],
                int(center[1]) + head_xy[1],
            ]
            nodes[side] = node
        return {
            "style": "acute_mecha",
            "lock_to_rig": True,
            "local_head_lock": True,
            "rig_brows": nodes,
            "ink_color": "#101216",
            "nameplate_bottom": head_xy[1] + nameplate_bottom,
        }
    return {
        "style": "acute_mecha",
        "ink_color": "#2B1A15",
        "gap_px": 3,
            "stroke_width_px": 9,
            "width_px": width_px,
            "nameplate_bottom": head_xy[1] + nameplate_bottom,
        }


def _ffmpeg() -> str:
    from .renderer import _resolve_ffmpeg

    return _resolve_ffmpeg()


def _probe_duration(path: Path) -> float:
    from moviepy import AudioFileClip

    clip = AudioFileClip(str(path))
    try:
        return float(clip.duration)
    finally:
        clip.close()


def _synthesize_line(text: str, voice: str, destination: Path) -> None:
    import edge_tts

    async def _save() -> None:
        communicate = edge_tts.Communicate(text, voice=voice, rate="+6%")
        await communicate.save(str(destination))

    asyncio.run(_save())


def build_exchange_audio(workdir: Path, lines: list[dict]) -> tuple[Path, list[dict]]:
    """Two short lines with a gap, plus the timeline the renderer should follow."""
    workdir.mkdir(parents=True, exist_ok=True)
    lead, gap, tail = 0.25, 0.45, 0.30
    cursor = lead
    timed: list[dict] = []
    for index, line in enumerate(lines):
        utterance = workdir / f"line_{index}.mp3"
        _synthesize_line(line["text"], line["voice"], utterance)
        duration = _probe_duration(utterance)
        timed.append({**line, "start": cursor, "end": cursor + duration, "audio": utterance})
        cursor += duration
        if index == 0:
            cursor += gap
    cursor += tail
    listing = workdir / "concat.txt"
    trim_silence = workdir / "lead.wav"
    gap_silence = workdir / "gap.wav"
    tail_silence = workdir / "tail.wav"
    for path, seconds in ((trim_silence, lead), (gap_silence, gap), (tail_silence, tail)):
        subprocess.run(
            [_ffmpeg(), "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", str(seconds), str(path)],
            check=True,
            capture_output=True,
        )
    # The concat demuxer drops clips when codecs differ, so every piece becomes WAV first.
    wavs: list[Path] = []
    for index, source in enumerate((trim_silence, timed[0]["audio"], gap_silence, timed[1]["audio"], tail_silence)):
        wav = workdir / f"piece_{index}.wav"
        subprocess.run(
            [_ffmpeg(), "-y", "-i", str(source), "-ar", "44100", "-ac", "2", str(wav)],
            check=True,
            capture_output=True,
        )
        wavs.append(wav)
    listing.write_text("".join(f"file '{path.as_posix()}'\n" for path in wavs), encoding="utf-8")
    mixed = workdir / "exchange.wav"
    completed = subprocess.run(
        [_ffmpeg(), "-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-c:a", "pcm_s16le", str(mixed)],
        check=True,
        capture_output=True,
        text=True,
    )
    duration = _probe_duration(mixed)
    if duration < 5.0:
        raise RuntimeError(f"exchange audio is {duration:.2f}s; speech clips were dropped")
    return mixed, timed


def _alias_voice(character_id: str, requested: str) -> str:
    """Map a preset name onto that puppet's reserved neural voice."""
    if requested in {"male_confident", "male_british", "reserved", ""}:
        return assign_debater_voices([character_id])[character_id]
    return requested


def debate_lines(
    *,
    left_id: str,
    right_id: str,
    voice_left: str,
    voice_right: str = "male_british",
    contraplano: bool,
) -> list[dict]:
    """Left speaker looks right and talks first. Right speaker looks left."""
    left = _CAST[left_id]
    right = _CAST[right_id]
    view_for_facing = {"right": "facing_right", "left": "facing_left"}
    voices = assign_debater_voices(
        [left_id, right_id],
        requested={left_id: _alias_voice(left_id, voice_left), right_id: _alias_voice(right_id, voice_right)},
    )
    rows = (
        (left_id, left, "right", voices[left_id]),
        (right_id, right, "left", voices[right_id]),
    )
    lines = []
    for character_id, card, facing, voice in rows:
        lines.append(
            {
                "character_id": character_id,
                "label": card["label"],
                "accent": card["accent"],
                "facing": facing,
                "voice": voice,
                "text": card["text"],
                "view": view_for_facing[facing] if contraplano else "facing_front",
            }
        )
    return lines


def render_test_dialogue(
    lines: list[dict],
    output_path: Path,
    *,
    contraplano: bool = False,
    scene: str = "random",
) -> Path:
    """Render a few seconds of ChatGPT answering Claude through the standard engine."""
    from . import render_dynamic_animation
    from .types import DialogueTurn, SpeakerStyle

    workdir = outputs_root() / "aiwake" / "_test_harness" / (
        "chatgpt_vs_claude_contraplano_build" if contraplano else "chatgpt_vs_claude_mouth_build"
    )
    skin_root = workdir / "puppets"
    for line in lines:
        build_render_skin(
            line["character_id"],
            skin_root,
            view_name=line["view"],
            contraplano=contraplano,
        )
    _write_pose_stills(skin_root, workdir / "mouth_pose_stills.png", [line["character_id"] for line in lines])
    mixed, timed = build_exchange_audio(workdir / "audio", lines)
    turns = [
        DialogueTurn(
            speaker=line["character_id"],
            start_time=line["start"],
            end_time=line["end"],
            text=line["text"],
            audio_path=str(line["audio"]),
        )
        for line in timed
    ]
    styles = [
        SpeakerStyle(
            character_id=line["character_id"],
            label=line["label"],
            accent_hex=line["accent"],
            facing=line["facing"],
        )
        for line in lines
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    render_dynamic_animation(
        turns=turns,
        audio_path=mixed,
        styles=styles,
        output_path=output_path,
        puppets_dir=skin_root,
        fps=30,
        width=1080,
        height=1920,
        burn_subtitles=True,
        use_rhubarb=True,
        scene=scene,
    )
    print(output_path)
    return output_path


def _write_pose_stills(skin_root: Path, destination: Path, character_ids: list[str] | None = None) -> None:
    """One open-mouth frame per puppet, so the chin seat can be checked."""
    from .puppet import PuppetRig, PuppetSkin

    frames = []
    for character_id in character_ids or ("chatgpt_cyborg_v1", "claude_cyborg_v1"):
        rig = PuppetRig(PuppetSkin.load(skin_root / character_id))
        frame = rig.compose(viseme="C", emotion="neutral")
        rgb = frame[..., :3]
        image = Image.fromarray(rgb)
        image.thumbnail((420, 760))
        frames.append(image)
    sheet = Image.new("RGB", (frames[0].width + frames[1].width, max(frame.height for frame in frames)), (8, 8, 10))
    x_pos = 0
    for frame in frames:
        sheet.paste(frame, (x_pos, 0))
        x_pos += frame.width
    destination.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(destination, compress_level=1)
    print(destination)


def build_solo_audio(
    workdir: Path,
    line: dict,
    *,
    target_s: float = 4.0,
) -> tuple[Path, dict]:
    """One utterance padded to the target. Speech is never trimmed."""
    workdir.mkdir(parents=True, exist_ok=True)
    lead = 0.15
    utterance = workdir / "line_0.mp3"
    _synthesize_line(line["text"], line["voice"], utterance)
    spoken = _probe_duration(utterance)
    tail = max(0.15, target_s - lead - spoken)
    if lead + spoken + tail > target_s + 0.6:
        tail = 0.15
    listing = workdir / "concat.txt"
    pieces = []
    for index, (source, seconds) in enumerate((("lead", lead), ("speech", spoken), ("tail", tail))):
        wav = workdir / f"piece_{index}.wav"
        if source == "speech":
            subprocess.run(
                [_ffmpeg(), "-y", "-i", str(utterance), "-ar", "44100", "-ac", "2", str(wav)],
                check=True,
                capture_output=True,
            )
        else:
            subprocess.run(
                [_ffmpeg(), "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", f"{seconds:.3f}", str(wav)],
                check=True,
                capture_output=True,
            )
        pieces.append(wav)
    listing.write_text("".join(f"file '{path.as_posix()}'\n" for path in pieces), encoding="utf-8")
    mixed = workdir / "solo.wav"
    subprocess.run(
        [_ffmpeg(), "-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-c:a", "pcm_s16le", str(mixed)],
        check=True,
        capture_output=True,
    )
    duration = _probe_duration(mixed)
    timed = {**line, "start": lead, "end": lead + spoken, "audio": utterance}
    _LOG.info("pilot audio %.2fs (speech %.2fs, voice %s)", duration, spoken, line["voice"])
    return mixed, timed


def render_deepseek_debut(output_path: Path, scene: str = "random") -> Path:
    """Five-second solo: Rhubarb visemes, blinks, and a deboche blade."""
    from . import render_dynamic_animation
    from .types import DialogueTurn, SpeakerStyle

    character_id = "deepseek_cyborg_v3"
    line = {
        "character_id": character_id,
        "label": "DEEPSEEK",
        "accent": "#C49A4E",
        "facing": "right",
        "voice": "en-US-ChristopherNeural",
        "text": "The clean path is the one that still holds.",
        "view": "facing_front",
    }
    workdir = output_path.parent / "deepseek_debut_build"
    skin_root = workdir / "puppets"
    build_render_skin(character_id, skin_root, view_name=line["view"], contraplano=False)
    mixed, timed = build_solo_audio(workdir / "audio", line, target_s=5.0)
    turns = [
        DialogueTurn(
            speaker=character_id,
            start_time=timed["start"],
            end_time=timed["end"],
            text=timed["text"],
            audio_path=str(timed["audio"]),
            emotion="deboche",
        )
    ]
    styles = [
        SpeakerStyle(
            character_id=character_id,
            label=line["label"],
            accent_hex=line["accent"],
            facing=line["facing"],
        )
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    render_dynamic_animation(
        turns=turns,
        audio_path=mixed,
        styles=styles,
        output_path=output_path,
        puppets_dir=skin_root,
        fps=30,
        width=1080,
        height=1920,
        burn_subtitles=True,
        use_rhubarb=True,
        scene=scene,
    )
    print(output_path)
    return output_path


def render_pilot_acting(character_id: str, output_path: Path, scene: str = "random") -> Path:
    """Four-second solo: reserved male voice, visemes, a cocked brow, and a blink."""
    from . import render_dynamic_animation
    from .types import DialogueTurn, SpeakerStyle

    card = _CAST[character_id]
    voice = assign_debater_voices([character_id])[character_id]
    line = {
        "character_id": character_id,
        "label": card["label"],
        "accent": card["accent"],
        "facing": "right",
        "voice": voice,
        "text": "A clear answer beats a clever dodge every single time.",
        "view": "facing_right",
    }
    workdir = outputs_root() / "aiwake" / "_test_harness" / "chatgpt_pilot_acting_build"
    skin_root = workdir / "puppets"
    build_render_skin(character_id, skin_root, view_name=line["view"], contraplano=True)
    mixed, timed = build_solo_audio(workdir / "audio", line)
    turns = [
        DialogueTurn(
            speaker=character_id,
            start_time=timed["start"],
            end_time=timed["end"],
            text=timed["text"],
            audio_path=str(timed["audio"]),
            emotion="skeptical",
        )
    ]
    styles = [
        SpeakerStyle(
            character_id=character_id,
            label=line["label"],
            accent_hex=line["accent"],
            facing="right",
        )
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    render_dynamic_animation(
        turns=turns,
        audio_path=mixed,
        styles=styles,
        output_path=output_path,
        puppets_dir=skin_root,
        fps=30,
        width=1080,
        height=1920,
        burn_subtitles=True,
        use_rhubarb=True,
        scene=scene,
    )
    print(output_path)
    return output_path


def render_v6_contraplano(output_path: Path, scene: str = "random") -> Path:
    """Six-second shot-reverse-shot using the approved V6 mouths, lids, and deboche."""
    from . import render_dynamic_animation
    from .types import DialogueTurn, SpeakerStyle

    lines = debate_lines(
        left_id="chatgpt_cyborg_v1",
        right_id="claude_cyborg_v1",
        voice_left="male_confident",
        voice_right="male_british",
        contraplano=True,
    )
    workdir = output_path.parent / "build"
    skin_root = workdir / "puppets"
    for line in lines:
        build_render_skin(
            line["character_id"],
            skin_root,
            view_name=line["view"],
            contraplano=True,
        )
    mixed, timed = build_exchange_audio(workdir / "audio", lines)
    duration = _probe_duration(mixed)
    if duration < 5.95:
        padded = workdir / "audio" / "exchange_6s.wav"
        subprocess.run(
            [
                _ffmpeg(),
                "-y",
                "-i",
                str(mixed),
                "-af",
                "apad=whole_dur=6",
                "-c:a",
                "pcm_s16le",
                str(padded),
            ],
            check=True,
            capture_output=True,
        )
        mixed = padded
    turns = [
        DialogueTurn(
            speaker=line["character_id"],
            start_time=line["start"],
            end_time=line["end"],
            text=line["text"],
            audio_path=str(line["audio"]),
            emotion="deboche",
        )
        for line in timed
    ]
    styles = [
        SpeakerStyle(
            character_id=line["character_id"],
            label=line["label"],
            accent_hex=line["accent"],
            facing=line["facing"],
        )
        for line in lines
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    render_dynamic_animation(
        turns=turns,
        audio_path=mixed,
        styles=styles,
        output_path=output_path,
        puppets_dir=skin_root,
        fps=30,
        width=1080,
        height=1920,
        burn_subtitles=True,
        use_rhubarb=True,
        scene=scene,
    )
    print(output_path)
    return output_path


def render_v7_contraplano(output_path: Path) -> Path:
    """Shot-reverse-shot from the current rig, with brows locked to that matrix."""
    return render_v6_contraplano(output_path)


def render_v8_contraplano(output_path: Path) -> Path:
    """Debate reel from the golden-master rig, brows locked to that matrix."""
    return render_v6_contraplano(output_path)


def render_v9_contraplano(output_path: Path) -> Path:
    """Debate reel from the production rig. Lids stay parented to the head."""
    return render_v6_contraplano(output_path)


def render_v10_contraplano(output_path: Path) -> Path:
    """Debate reel from the release-candidate rig."""
    return render_v6_contraplano(output_path)


def render_v11_contraplano(output_path: Path) -> Path:
    """Debate reel from the gold-master rig."""
    return render_v6_contraplano(output_path)


def render_v12_contraplano(output_path: Path) -> Path:
    """Debate reel from the definitive-release rig."""
    return render_v6_contraplano(output_path)


def render_v13_contraplano(output_path: Path) -> Path:
    """Debate reel from the golden-seal rig."""
    return render_v6_contraplano(output_path)


def render_v14_contraplano(output_path: Path) -> Path:
    """Debate reel from the master sign-off rig."""
    return render_v6_contraplano(output_path)


SCENE_02_NAME = "scene_02_steampunk_observatory.png"
SCENE_PREVIEW = (
    outputs_root()
    / "aiwake"
    / "_test_harness"
    / "scene_test"
    / "chatgpt_claude_new_scene_preview.png"
)
_SCENE_STAGE_HEIGHT = 0.68
_SCENE_GROUND_MARGIN = 36


def _scene_dir() -> Path:
    return assets_root() / "backgrounds"


def _scene_02_path() -> Path:
    return _scene_dir() / SCENE_02_NAME


def _write_scene_registry(scene_path: Path) -> Path:
    """Register the observatory beside the locked library panorama."""
    library = (
        assets_root()
        / "puppets"
        / "shared_backgrounds"
        / "aiwake_arena_panorama_v2.png"
    )
    registry = {
        "scenes": [
            {
                "id": "scene_01_library_cathedral",
                "file": library.relative_to(assets_root()).as_posix(),
                "locked": True,
            },
            {
                "id": "scene_02_steampunk_observatory",
                "file": scene_path.relative_to(assets_root()).as_posix(),
                "size": [1080, 1920],
                "locked": True,
            },
        ]
    }
    destination = _scene_dir() / "registry.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(registry, indent=2), encoding="utf-8")
    return destination


def _observatory_source() -> Path | None:
    candidates = (
        Path.home()
        / ".cursor"
        / "projects"
        / "c-dev-omni-engine"
        / "assets"
        / SCENE_02_NAME,
        Path(__file__).resolve().parents[2] / "assets" / SCENE_02_NAME,
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def generate_steampunk_observatory() -> Path:
    """Retired. Shared scenes live only in puppets/shared_backgrounds."""
    raise RuntimeError(
        "assets/backgrounds is retired; write shared scenes under puppets/shared_backgrounds"
    )


def _ground_character(
    plate: Image.Image,
    sprite: Image.Image,
    center_x: int,
) -> None:
    """Fit one figure into its half of the stage, then apply the +5% scale."""
    alpha = np.asarray(sprite)[..., 3]
    ys, xs = np.nonzero(alpha > 16)
    if xs.size == 0:
        return
    crop = sprite.crop((int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1))
    slot_w = int(round(plate.width * 0.48))
    slot_h = int(round(plate.height * _SCENE_STAGE_HEIGHT))
    scale = min(slot_w / crop.width, slot_h / crop.height) * 1.05
    fitted = crop.resize(
        (
            max(1, int(round(crop.width * scale))),
            max(1, int(round(crop.height * scale))),
        ),
        Image.Resampling.LANCZOS,
    )
    left = int(round(center_x - fitted.width / 2))
    left = max(8, min(left, plate.width - fitted.width - 8))
    top = plate.height - _SCENE_GROUND_MARGIN - fitted.height
    plate.alpha_composite(fitted, (left, max(0, top)))


def preview_scene_composite(
    scene_path: Path,
    destination: Path,
    *,
    speaker_left: str,
    speaker_right: str,
) -> Path:
    """One still: ChatGPT on the left looking right, Claude on the right looking left."""
    from .puppet import PuppetRig, PuppetSkin

    workdir = destination.parent / "build" / "puppets"
    build_render_skin(speaker_left, workdir, view_name="facing_right", contraplano=True)
    build_render_skin(speaker_right, workdir, view_name="facing_left", contraplano=True)
    with Image.open(scene_path) as opened:
        plate = opened.convert("RGBA")
    if plate.size != (1080, 1920):
        plate = ImageOps.fit(plate, (1080, 1920), method=Image.Resampling.LANCZOS)
    for character_id, center_x in ((speaker_left, 270), (speaker_right, 810)):
        rig = PuppetRig(PuppetSkin.load(workdir / character_id))
        frame = rig.compose(viseme="X", emotion="neutral")
        rgb = np.clip(frame[..., :3], 0, 255).astype(np.uint8)
        alpha = np.clip(frame[..., 3], 0, 255).astype(np.uint8)
        sprite = Image.fromarray(np.dstack((rgb, alpha)))
        _ground_character(plate, sprite, center_x)
    destination.parent.mkdir(parents=True, exist_ok=True)
    plate.convert("RGB").save(destination, format="PNG", compress_level=1)
    print(destination)
    return destination


def _shared_backgrounds() -> Path:
    return assets_root() / "puppets" / "shared_backgrounds"


APPROVED_ARENA_NAMES: tuple[str, ...] = (
    "aiwake_arena_panorama_v2.png",
    "aiwake_arena_panorama_v3.png",
    "arena_04_clockwork_foundry.png",
    "arena_05_botanical_conservatory.png",
    "arena_06_royal_circular_vault.png",
    "arena_07_council_chamber.png",
    "arena_08_subterranean_relay.png",
    "arena_09_sky_armory_hangar.png",
    "arena_10_celestial_cartography.png",
    "arena_11_alchemical_apothecary.png",
    "arena_12_clock_tower_interior.png",
    "arena_13_grand_greenhouse_atrium.png",
    "arena_14_telegraphic_exchange.png",
    "arena_15_chamber_of_reason.png",
)


def approved_arenas_pool() -> list[Path]:
    """Official graded panoramas. Raw ``*_source.png`` plates are excluded."""
    root = _shared_backgrounds()
    pool = [root / name for name in APPROVED_ARENA_NAMES]
    missing = [path.name for path in pool if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"approved arenas missing: {', '.join(missing)}")
    return pool


def get_scene_by_name(scene_arg: str) -> Path:
    token = scene_arg.strip()
    if token.lower().endswith(".png"):
        token = token[:-4]
    pool = approved_arenas_pool()
    for path in pool:
        if path.stem == token or path.name == scene_arg.strip():
            return path
    raise FileNotFoundError(f"arena {scene_arg} is not in the approved pool")


def resolve_scene_panorama(scene_arg: str = "random") -> Path:
    """Pick one official arena. ``random`` is the default when no scene is named."""
    if scene_arg == "random" or not scene_arg:
        chosen_scene = random.choice(approved_arenas_pool())
        logging.getLogger("animator.pipeline").info(
            "[ARENA SELECTOR] Sorteando arena automática: %s",
            chosen_scene.name,
        )
        return chosen_scene
    return get_scene_by_name(scene_arg)


def verify_arena_camera_crops() -> None:
    """Each official plate must crop to the two 1080x1920 reverse angles."""
    from .compositor import ShotReverseShotCompositor
    from .types import SpeakerStyle

    director = ShotReverseShotCompositor.__new__(ShotReverseShotCompositor)
    director.width = 1080
    director.height = 1920
    for path in approved_arenas_pool():
        with Image.open(path) as opened:
            panorama = opened.convert("RGB")
        if panorama.size != (2160, 1920):
            raise RuntimeError(f"{path.name} is {panorama.size}, expected 2160x1920")
        for facing in ("right", "left"):
            background = director._prepare_camera_background(
                None,
                SpeakerStyle(character_id="arena", label="ARENA", facing=facing),
                panorama,
            )
            if background.shape != (1920, 1080, 3):
                raise RuntimeError(
                    f"{path.name} facing={facing} crop is {background.shape}"
                )
    print(f"camera crops ok: {len(APPROVED_ARENA_NAMES)} arenas")


def _panorama_v2() -> Path:
    return _shared_backgrounds() / "aiwake_arena_panorama_v2.png"


def _panorama_v3() -> Path:
    return _shared_backgrounds() / "aiwake_arena_panorama_v3.png"


def clean_rogue_backgrounds() -> None:
    """Delete the duplicate assets/backgrounds tree. Shared scenes stay."""
    rogue = assets_root() / "backgrounds"
    shared = _shared_backgrounds()
    if rogue.resolve() == shared.resolve():
        raise RuntimeError("refusing to delete the canonical shared_backgrounds directory")
    if rogue.is_dir():
        shutil.rmtree(rogue)
        print(f"removed rogue backgrounds: {rogue}")
    else:
        print(f"rogue backgrounds already absent: {rogue}")


_PANORAMA_V3_PROMPT = (
    "1990s Studio Ghibli painted background art, Hayao Miyazaki Laputa aesthetic. "
    "Rich hand-painted gouache, warm golden-hour lighting, dark anime ink linework. "
    "A grand panoramic observation bridge and clockwork library inside a Victorian "
    "steampunk sky cruiser, one continuous shared hall. "
    "Left section: mahogany celestial navigation tables, antique star charts, "
    "brass astrolabes, and green banker lamps. "
    "Center vista: grand arched bay windows filled with Art Nouveau Gothic stained glass "
    "in warm gold, amber, and olive, filtering stationary sunset light across "
    "the polished wooden floor. "
    "Right section: steampunk pressure consoles, glowing brass gauges, copper tubes, "
    "and warm amber vacuum tubes. "
    "Completely clean empty arena. No human figures. No robot figures."
)


def audit_bg_generator() -> dict:
    """Report the live environment generator and the locked v2 plate size."""
    from .factory.create_environment import PANORAMA_SIZE, generation_aspect_for_size

    with Image.open(_panorama_v2()) as opened:
        locked = opened.size
    report = {
        "module": "core.animator.factory.create_environment",
        "function": "create_environment",
        "backend": "core.animator.factory.create_puppet.generate_character_image",
        "model": "models/gemini-2.5-flash-image",
        "formatter": "apply_reverse_angle_ambience",
        "fit": "ImageOps.fit uniform cover-crop, shared scale on both axes",
        "default_panorama_size": list(PANORAMA_SIZE),
        "canonical_v2_size": [locked[0], locked[1]],
        "locked_output_size": [locked[0], locked[1]],
        "generation_aspect_ratio": generation_aspect_for_size(locked),
        "destination": str(_panorama_v3()),
    }
    print(json.dumps(report, indent=2))
    return report


def generate_panorama_v3() -> Path:
    """Run create_environment and lock the plate to the v2 pixel size."""
    from dotenv import load_dotenv

    from .factory.create_environment import create_environment

    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    canonical = _panorama_v2()
    before = canonical.read_bytes()
    with Image.open(canonical) as opened:
        locked = opened.size
    result = create_environment(
        theme_id="aiwake_arena_panorama_v3",
        prompt=_PANORAMA_V3_PROMPT,
        output_size=locked,
    )
    destination = Path(result["path"])
    if destination.resolve() != _panorama_v3().resolve():
        raise RuntimeError(f"generator wrote {destination}, expected {_panorama_v3()}")
    if tuple(result["size"]) != locked:
        raise RuntimeError(f"panorama v3 is {result['size']}, expected {list(locked)}")
    if canonical.read_bytes() != before:
        raise RuntimeError(f"refusing to alter the canonical panorama: {canonical}")
    print(f"{destination} {locked[0]}x{locked[1]} aspect={result['aspect_ratio']}")
    return destination


_GHIBLI_STYLE = (
    "1990s Studio Ghibli painted background art, Hayao Miyazaki Laputa aesthetic. "
    "Rich hand-painted gouache and watercolor, warm golden-hour lighting, dark anime ink linework. "
    "Completely empty arena. No human figures. No robot figures. No letters."
)
_SCENE_CATALOG: tuple[dict[str, str], ...] = (
    {
        "theme_id": "aiwake_arena_panorama_v3",
        "title": "03  Royal Airship Observatory",
        "reference": "window",
        "prompt": (
            f"{_GHIBLI_STYLE} "
            "Revise the reference image. Keep the magnificent interior: mahogany celestial "
            "navigation tables, antique star charts, brass astrolabes, and green banker lamps "
            "on the left; steampunk pressure consoles, glowing brass gauges, copper tubes, and "
            "warm amber vacuum tubes on the right; polished herringbone wood floor; vaulted "
            "wooden ceiling; book-lined walls. "
            "REPLACE ONLY the central bay window. Remove every fluffy cloud and every sea of clouds. "
            "Fill those arched windows with grand Art Nouveau Gothic stained glass vitrais: "
            "multi-pane ornamental leaded glass in warm golden, amber, and olive patterns that "
            "filter stationary sunset light onto the floor. The glass is solid, still, and architectural."
        ),
    },
    {
        "theme_id": "scene_04_clockwork_foundry",
        "title": "04  A Oficina Real de Autômatos",
        "reference": "",
        "prompt": (
            f"{_GHIBLI_STYLE} "
            "A Oficina Real de Autômatos. Vaulted stone and exposed iron arches. "
            "Center: a giant brass pendulum frozen at rest and a hanging astronomical orrery, both still. "
            "Left: drafting tables, parchment blueprints, brass calipers. "
            "Right: testing benches, glowing nixie tube arrays, copper steam pistons at rest. "
            "No steam plumes. Enclosed foundry interior with a clear standing floor."
        ),
    },
    {
        "theme_id": "scene_05_botanical_conservatory",
        "title": "05  O Jardim de Inverno Mecânico",
        "reference": "",
        "prompt": (
            f"{_GHIBLI_STYLE} "
            "O Jardim de Inverno Mecânico. Ornate Victorian wrought-iron and glass dome. "
            "Large arched windows look onto stationary stone statues, stone balustrades, and a "
            "distant silent mountain ridge under a smooth twilight gradient sky with no cloud forms. "
            "Terrariums, brass watering apparatus, antique reading nooks. "
            "No spraying water, no wind-blown leaves. Open floor between the planters."
        ),
    },
    {
        "theme_id": "scene_06_grand_circular_vault",
        "title": "06  O Grande Arquivo Circular",
        "reference": "",
        "prompt": (
            f"{_GHIBLI_STYLE} "
            "O Grande Arquivo Circular. A completely enclosed interior rotunda. "
            "Solid wood and plaster dome ceiling with painted coffers. "
            "Absolutely no windows, no glass panes, no stained glass, no sky, no clouds, "
            "and no exterior view of any kind. "
            "Multi-tier curved mahogany balconies, wrought-iron spiral staircases, a central "
            "illuminated celestial brass globe glowing softly, and warm desk lamps. "
            "The only light comes from interior lamps. Polished wood floor, still air."
        ),
    },
    {
        "theme_id": "scene_07_council_chamber",
        "title": "07  O Salão de Honra dos Filósofos",
        "reference": "",
        "prompt": (
            f"{_GHIBLI_STYLE} "
            "O Salão de Honra dos Filósofos. Renaissance wood-paneled walls, rich crimson drapery, "
            "and heraldic brass crests. A central stained-glass rose window filters deep amber and "
            "emerald light across a herringbone parquet floor. No open sky. Empty ceremonial hall."
        ),
    },
    {
        "theme_id": "scene_08_subterranean_relay",
        "title": "08  A Estação de Relés e Válvulas",
        "reference": "",
        "prompt": (
            f"{_GHIBLI_STYLE} "
            "A Estação de Relés e Válvulas. Subterranean warm industrial Ghibli archive. "
            "Granite flagstone floor, massive glowing vacuum-tube mainframe banks with warm orange "
            "filaments, copper conduit pipes, and antique teletype stations. "
            "Enclosed underground hall. No smoke, no steam, no water."
        ),
    },
)


def _locked_panorama_size() -> tuple[int, int]:
    with Image.open(_panorama_v2()) as opened:
        return opened.size


def _catalog_font(size: int) -> ImageFont.ImageFont:
    for candidate in (
        Path(r"C:\Windows\Fonts\segoeui.ttf"),
        Path(r"C:\Windows\Fonts\arial.ttf"),
    ):
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


def generate_scene_catalog() -> list[Path]:
    """Revise scene 03 and write scenes 04–08 at the canonical v2 plate size."""
    from dotenv import load_dotenv

    from .factory.create_environment import (
        STATIC_SCENE_RULES,
        create_environment,
        environment_prompt,
    )

    if STATIC_SCENE_RULES not in environment_prompt("catalog"):
        raise RuntimeError("STATIC_SCENE_RULES is missing from the environment prompt")
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    canonical = _panorama_v2()
    before = canonical.read_bytes()
    locked = _locked_panorama_size()
    snapshot_dir = (
        outputs_root() / "aiwake" / "_test_harness" / "scene_test" / "build"
    )
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for scene in _SCENE_CATALOG:
        reference: Path | None = None
        if scene["reference"] == "window":
            current = _shared_backgrounds() / f"{scene['theme_id']}.png"
            if not current.is_file():
                raise FileNotFoundError(current)
            reference = snapshot_dir / f"{scene['theme_id']}_window_reference.png"
            reference.write_bytes(current.read_bytes())
        result = create_environment(
            theme_id=scene["theme_id"],
            prompt=scene["prompt"],
            output_size=locked,
            reference_path=reference,
        )
        destination = Path(result["path"])
        if tuple(result["size"]) != locked:
            raise RuntimeError(
                f"{destination.name} is {result['size']}, expected {list(locked)}"
            )
        if canonical.read_bytes() != before:
            raise RuntimeError(f"refusing to alter the canonical panorama: {canonical}")
        print(f"{destination} {locked[0]}x{locked[1]} aspect={result['aspect_ratio']}")
        written.append(destination)
    return written


def inspect_scene_catalog(destination: Path | None = None) -> Path:
    """Stack the six catalog plates with a title on each cell."""
    locked = _locked_panorama_size()
    destination = destination or (
        outputs_root()
        / "aiwake"
        / "_test_harness"
        / "scene_test"
        / "scene_catalog_preview_sheet.png"
    )
    columns = 2
    thumb_w = 960
    thumb_h = int(round(locked[1] * (thumb_w / locked[0])))
    label_h = 64
    pad = 18
    rows = (len(_SCENE_CATALOG) + columns - 1) // columns
    sheet = Image.new(
        "RGB",
        (
            columns * thumb_w + (columns + 1) * pad,
            rows * (thumb_h + label_h) + (rows + 1) * pad,
        ),
        (28, 22, 16),
    )
    draw = ImageDraw.Draw(sheet)
    font = _catalog_font(32)
    for index, scene in enumerate(_SCENE_CATALOG):
        path = _shared_backgrounds() / f"{scene['theme_id']}.png"
        with Image.open(path) as opened:
            if opened.size != locked:
                raise RuntimeError(f"{path.name} is {opened.size}, expected {locked}")
            thumb = opened.convert("RGB").resize(
                (thumb_w, thumb_h),
                Image.Resampling.LANCZOS,
            )
        column = index % columns
        row = index // columns
        x = pad + column * (thumb_w + pad)
        y = pad + row * (thumb_h + label_h + pad)
        draw.rectangle((x, y, x + thumb_w, y + label_h), fill=(72, 48, 28))
        draw.text((x + 18, y + 14), scene["title"], fill=(245, 228, 196), font=font)
        sheet.paste(thumb, (x, y + label_h))
    destination.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(destination, format="PNG", compress_level=1)
    print(f"{destination} {sheet.size[0]}x{sheet.size[1]}")
    return destination


def preview_contraplano_crops(
    panorama_path: Path,
    destination: Path,
    *,
    speaker_left: str,
    speaker_right: str,
) -> Path:
    """Left camera on the panorama's left half, reverse camera on the right half."""
    from .compositor import ShotReverseShotCompositor
    from .puppet import PuppetRig, PuppetSkin
    from .types import SpeakerStyle

    workdir = destination.parent / "build" / "puppets"
    build_render_skin(speaker_left, workdir, view_name="facing_right", contraplano=True)
    build_render_skin(speaker_right, workdir, view_name="facing_left", contraplano=True)
    with Image.open(panorama_path) as opened:
        panorama = opened.convert("RGB")
    director = ShotReverseShotCompositor.__new__(ShotReverseShotCompositor)
    director.width = panorama.width // 2
    director.height = panorama.height
    shots = (
        (
            speaker_left,
            SpeakerStyle(character_id=speaker_left, label="CHATGPT", facing="right"),
            director.width // 2,
        ),
        (
            speaker_right,
            SpeakerStyle(character_id=speaker_right, label="CLAUDE", facing="left"),
            director.width // 2,
        ),
    )
    panels: list[Image.Image] = []
    for character_id, style, center_x in shots:
        rig = PuppetRig(PuppetSkin.load(workdir / character_id))
        background = director._prepare_camera_background(rig, style, panorama)
        plate = Image.fromarray(background).convert("RGBA")
        frame = rig.compose(viseme="X", emotion="neutral")
        rgb = np.clip(frame[..., :3], 0, 255).astype(np.uint8)
        alpha = np.clip(frame[..., 3], 0, 255).astype(np.uint8)
        sprite = Image.fromarray(np.dstack((rgb, alpha)))
        _ground_character(plate, sprite, center_x)
        panels.append(plate.convert("RGB"))
    preview = Image.new("RGB", (sum(panel.width for panel in panels), panels[0].height))
    cursor = 0
    for panel in panels:
        preview.paste(panel, (cursor, 0))
        cursor += panel.width
    destination.parent.mkdir(parents=True, exist_ok=True)
    preview.save(destination, format="PNG", compress_level=1)
    print(f"{destination} {preview.size[0]}x{preview.size[1]}")
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render a ChatGPT vs Claude debate test.")
    parser.add_argument("--clone-from", default="llama")
    parser.add_argument("--puppets", default="", help="Comma-separated puppet ids.")
    parser.add_argument("--contraplano", action="store_true")
    parser.add_argument("--scene", default="random")
    parser.add_argument("--speaker-left", default="chatgpt_cyborg_v1")
    parser.add_argument("--speaker-right", default="claude_cyborg_v1")
    parser.add_argument("--voice-left", default="male_confident")
    parser.add_argument("--voice-right", default="male_british")
    parser.add_argument("--render-test-dialogue", action="store_true")
    parser.add_argument("--pilot", default="")
    parser.add_argument("--draw-mouths", action="store_true")
    parser.add_argument("--inspect-sheet", action="store_true")
    parser.add_argument("--render-pilot-acting", action="store_true")
    parser.add_argument("--freeze-deepseek", action="store_true")
    parser.add_argument("--generate-new-scene", action="store_true")
    parser.add_argument("--preview-scene-composite", action="store_true")
    parser.add_argument("--clean-rogue-bg", action="store_true")
    parser.add_argument("--audit-bg-generator", action="store_true")
    parser.add_argument("--generate-panorama-v3", action="store_true")
    parser.add_argument("--preview-contraplano-crops", action="store_true")
    parser.add_argument("--generate-scene-catalog", action="store_true")
    parser.add_argument("--apply-static-rules", action="store_true")
    parser.add_argument("--inspect-catalog", action="store_true")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if args.generate_scene_catalog or args.apply_static_rules or args.inspect_catalog:
        if args.generate_scene_catalog and not args.apply_static_rules:
            parser.error("--generate-scene-catalog requires --apply-static-rules")
        if args.apply_static_rules:
            from .factory.create_environment import STATIC_SCENE_RULES, environment_prompt

            if STATIC_SCENE_RULES not in environment_prompt("static-rules"):
                raise RuntimeError("STATIC_SCENE_RULES is missing from the environment prompt")
            print("STATIC_SCENE_RULES armed")
        if args.generate_scene_catalog:
            generate_scene_catalog()
        if args.inspect_catalog:
            inspect_scene_catalog(args.output)
        return 0
    if (
        args.audit_bg_generator
        or args.clean_rogue_bg
        or args.generate_panorama_v3
        or args.preview_contraplano_crops
    ):
        if args.audit_bg_generator:
            audit_bg_generator()
        if args.clean_rogue_bg:
            clean_rogue_backgrounds()
        panorama = _panorama_v3()
        if args.generate_panorama_v3 or (
            args.preview_contraplano_crops and not panorama.is_file()
        ):
            panorama = generate_panorama_v3()
        if args.preview_contraplano_crops:
            preview_contraplano_crops(
                panorama,
                outputs_root()
                / "aiwake"
                / "_test_harness"
                / "scene_test"
                / "panorama_v3_preview.png",
                speaker_left=args.speaker_left,
                speaker_right=args.speaker_right,
            )
        return 0
    if args.freeze_deepseek or args.generate_new_scene or args.preview_scene_composite:
        scene_path = _scene_02_path()
        if args.freeze_deepseek:
            from .vision.head_analyzer import freeze_gold_master

            freeze_gold_master("deepseek_cyborg_v3")
        if args.generate_new_scene or args.preview_scene_composite:
            if args.generate_new_scene or not scene_path.is_file():
                scene_path = generate_steampunk_observatory()
        if args.preview_scene_composite:
            preview_scene_composite(
                scene_path,
                args.output or SCENE_PREVIEW,
                speaker_left=args.speaker_left,
                speaker_right=args.speaker_right,
            )
        return 0
    if args.pilot:
        if args.draw_mouths:
            draw_mouths(args.pilot, {"style": "cybernetic_capsule"})
            generate_character_acting(args.pilot)
            install_view_anchors(args.pilot)
        if args.inspect_sheet:
            export_pilot_facial_sheet(args.pilot, _PILOT_SHEET)
        if args.render_pilot_acting:
            render_pilot_acting(args.pilot, args.output or _PILOT_VIDEO, scene=args.scene)
        if not (args.draw_mouths or args.inspect_sheet or args.render_pilot_acting):
            parser.error("--pilot needs --draw-mouths, --inspect-sheet, or --render-pilot-acting")
        return 0
    if args.contraplano:
        puppets = [args.speaker_left, args.speaker_right]
        for character_id in puppets:
            generate_viseme_set(character_id)
            generate_character_acting(character_id)
        if args.render_test_dialogue:
            lines = debate_lines(
                left_id=args.speaker_left,
                right_id=args.speaker_right,
                voice_left=args.voice_left,
                voice_right=args.voice_right,
                contraplano=True,
            )
            render_test_dialogue(
                lines,
                args.output or _CONTRAPLANO_OUTPUT,
                contraplano=True,
                scene=args.scene,
            )
        return 0
    if not args.puppets:
        parser.error("pass --puppets or --contraplano")
    puppets = [resolve_puppet(token) for token in args.puppets.split(",") if token.strip()]
    clone_mouths(args.clone_from, puppets)
    if args.render_test_dialogue:
        lines = debate_lines(
            left_id=puppets[0],
            right_id=puppets[-1],
            voice_left="male_confident",
            contraplano=False,
        )
        render_test_dialogue(
            lines,
            args.output or _TEST_OUTPUT,
            contraplano=False,
            scene=args.scene,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
