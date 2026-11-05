"""Render a scene spec and an audio file to an MP4.

Frames are pulled one at a time. ffmpeg is always given a timeout, and a
failed encode does not copy the remaining frames into RAM.
"""
from __future__ import annotations

import json
import logging
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from .animator_types import DialogueTurn
from .audio_analyzer import AudioAnalyzer
from .neutral_skin import paint_placeholder_layers
from .puppet import PuppetRig, PuppetSkin
from .renderer import AnimationRenderer, RenderStats, RenderTimeout
from .scene_compositor import SceneCompositor
from .scene_spec import LineSpec, SceneSpec, load_scene, preview_geometry

_LOG = logging.getLogger("animator.scene")

_OPEN_END_S = 1_000_000.0
_DECODE_TIMEOUT_S = 60.0


def encode_budget_s(duration_s: float, *, preview: bool) -> float:
    """Wall-clock cap for one encode. Preview is the cheaper path."""
    per_second = 8.0 if preview else 20.0
    return max(30.0, 15.0 + max(0.0, float(duration_s)) * per_second)


def render_scene(
    scene: SceneSpec | str | Path | Mapping[str, Any],
    audio_path: str | Path,
    output_path: str | Path,
    *,
    preview: bool = False,
    seed: int | None = None,
    timeout_s: float | None = None,
) -> RenderStats:
    """Render ``scene`` against ``audio_path``.

    ``preview`` halves the frame and caps the rate at 12 fps, and encodes
    with a fast preset. The same spec, audio, seed, and preview flag render
    the same file. ``timeout_s`` bounds every ffmpeg call in this render.
    """
    spec = load_scene(scene)
    if seed is not None:
        spec = replace(spec, seed=int(seed))
    if timeout_s is not None and float(timeout_s) <= 0:
        raise ValueError("timeout_s must be positive")
    audio = Path(audio_path)
    if not audio.is_file():
        raise FileNotFoundError(f"audio file not found: {audio}")

    width, height, fps = spec.width, spec.height, spec.fps
    preset, crf, tune = "veryfast", 17, "grain"
    if preview:
        width, height, fps = preview_geometry(width, height, fps)
        preset, crf, tune = "ultrafast", 28, None

    started = time.monotonic()

    def take_timeout(default: float) -> float:
        if timeout_s is None:
            return default
        left = float(timeout_s) - (time.monotonic() - started)
        if left <= 0:
            raise RenderTimeout(float(timeout_s))
        return left

    lines = effective_lines(spec)
    with tempfile.TemporaryDirectory(prefix="rigly_scene_") as temp_name:
        temp = Path(temp_name)
        wav_path = _as_wav(audio, temp / "session.wav", take_timeout(_DECODE_TIMEOUT_S))
        rigs = _load_rigs(spec, temp / "puppets")
        analyzed = AudioAnalyzer(fps=fps, seed=spec.seed).analyze(
            wav_path,
            _turns(lines, spec.lip_sync),
            use_rhubarb=False,
        )
        compositor = SceneCompositor(
            spec=spec,
            rigs=rigs,
            lines=lines,
            width=width,
            height=height,
        )
        _LOG.info(
            "scene render %sx%s @ %sfps preview=%s duration=%.2fs",
            width,
            height,
            fps,
            preview,
            analyzed.duration_s,
        )
        renderer = AnimationRenderer(width=width, height=height, fps=fps)
        return renderer.render(
            frame_iter=compositor.iter_frames(analyzed),
            audio_path=wav_path,
            output_path=Path(output_path),
            duration_s=analyzed.duration_s,
            preset=preset,
            crf=crf,
            gop=fps,
            tune=tune,
            threads=1,
            deterministic=True,
            encode_timeout_s=take_timeout(
                encode_budget_s(analyzed.duration_s, preview=preview)
            ),
            retain_frames=False,
        )


def effective_lines(spec: SceneSpec) -> tuple[LineSpec, ...]:
    """Lines to perform. An empty ledger speaks the focus character for the whole file."""
    if spec.lines:
        return spec.lines
    speaker = spec.focus_id
    character = next(item for item in spec.characters if item.id == speaker)
    return (
        LineSpec(
            speaker=speaker,
            start=0.0,
            end=None,
            text=character.label,
            emotion="neutral",
        ),
    )


def _turns(lines: tuple[LineSpec, ...], lip_sync: str) -> list[DialogueTurn]:
    turns: list[DialogueTurn] = []
    for line in lines:
        text = "" if lip_sync == "off" else line.text
        turns.append(
            DialogueTurn(
                speaker=line.speaker,
                start_time=line.start,
                end_time=_OPEN_END_S if line.end is None else line.end,
                text=text,
                emotion=line.emotion,
            )
        )
    return turns


def _load_rigs(spec: SceneSpec, work: Path) -> dict[str, PuppetRig]:
    rigs: dict[str, PuppetRig] = {}
    for character in spec.characters:
        if character.puppet:
            root = Path(character.puppet)
            if not root.is_absolute():
                root = spec.base_dir / root
            if root.exists() and not root.is_dir():
                raise ValueError(f"puppet path for {character.id} is not a directory: {root}")
            if _needs_generated_skin(root):
                rigs[character.id] = PuppetRig(_generated_puppet(character, root))
            else:
                rigs[character.id] = PuppetRig(PuppetSkin.load_or_create(root))
        else:
            rigs[character.id] = PuppetRig(_generated_puppet(character, work / character.id))
    return rigs


def _needs_generated_skin(root: Path) -> bool:
    if not root.exists():
        return True
    return not any(root.iterdir())


def _generated_puppet(character, root: Path) -> PuppetSkin:
    """Paint the neutral stand-in without asking a product asset provider."""
    from .puppet import _placeholder_ensure_manifest  # noqa: PLC0415

    root.mkdir(parents=True, exist_ok=True)
    manifest_path = _placeholder_ensure_manifest(root)
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["character_id"] = character.id
    theme = dict(data.get("theme") or {})
    theme["glow_color"] = character.accent
    data["theme"] = theme
    manifest_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    skin = PuppetSkin.load(root)
    paint_placeholder_layers(skin, missing=skin.missing_layers())
    return skin


def _as_wav(audio_path: Path, dest: Path, timeout: float) -> Path:
    """Use a wav as-is. Other containers are decoded with a bounded ffmpeg call."""
    if audio_path.suffix.lower() == ".wav":
        return audio_path
    from shared.ffmpeg import resolve_ffmpeg, run_ffmpeg  # noqa: PLC0415

    dest.parent.mkdir(parents=True, exist_ok=True)
    result = run_ffmpeg(
        [
            resolve_ffmpeg(),
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(audio_path),
            "-ac",
            "1",
            "-ar",
            "44100",
            str(dest),
        ],
        timeout=timeout,
        text=True,
    )
    detail = (result.stderr or "").strip()
    if result.returncode != 0 or not dest.is_file():
        raise RuntimeError(detail[-500:] or f"could not decode {audio_path.name}")
    return dest


__all__ = ["encode_budget_s", "effective_lines", "render_scene"]
