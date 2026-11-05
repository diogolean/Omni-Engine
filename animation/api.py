"""Public facade over the parametric engine.

``render_dynamic_animation`` keeps today's signature. ``render_scene`` is the
declarative entry: a JSON or YAML spec plus an audio file. A product may
register a scene resolver; this module does not know arena filenames. The
picture is always an ffmpeg pipe
(see :class:`animation.parametric.renderer.AnimationRenderer`).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Sequence

from animation.parametric.animator_types import DialogueTurn, SpeakerStyle
from animation.parametric.audio_analyzer import AudioAnalyzer
from animation.parametric.compositor import (
    DualPresenceCompositor,
    ShotReverseShotCompositor,
)
from animation.parametric.puppet import PuppetRig, PuppetSkin
from animation.parametric.renderer import AnimationRenderer, RenderStats
from animation.parametric.scene_render import render_scene
from animation.parametric.scene_spec import SceneSpec, load_scene
from animation.parametric.subtitles import build_ass

_LOG = logging.getLogger("animator")

_SCENE_RESOLVER: Callable[[str], Path] | None = None


def register_scene_resolver(resolver: Callable[[str], Path]) -> None:
    """Register the product that turns a scene name into a panorama path."""
    global _SCENE_RESOLVER
    _SCENE_RESOLVER = resolver


def _default_puppets_dir() -> Path:
    """``{ASSETS_PATH}/puppets``, with a package-local fallback."""
    try:
        from shared.paths import assets_root  # noqa: PLC0415

        return assets_root() / "puppets"
    except Exception as exc:  # noqa: BLE001 — standalone package, no factory paths
        _LOG.warning(
            "shared.paths unavailable (%s); falling back to a local assets/puppets dir",
            exc,
        )
        return Path(__file__).resolve().parent / "assets" / "puppets"


def render_dynamic_animation(
    *,
    turns: Sequence[DialogueTurn],
    audio_path,
    styles: Sequence[SpeakerStyle],
    output_path,
    puppets_dir=None,
    fps: int = 30,
    width: int = 1080,
    height: int = 1920,
    duration_override: float | None = None,
    seed: int = 0,
    burn_subtitles: bool = True,
    use_rhubarb: bool = True,
    enable_cta: bool = False,
    outro_start_s: float | None = None,
    outro_frame=None,
    subtitle_fade_s: float = 0.0,
    scene: str | None = None,
    panorama_path=None,
    stage_mode: str = "shot_reverse_shot",
) -> RenderStats:
    """Analyze audio, direct the shot-reverse-shot, render to mp4.

    ``scene`` is resolved only when a product registered a resolver (the
    ``core.animator`` facade does). Pass ``panorama_path`` to skip that.
    """
    puppets_root = Path(puppets_dir) if puppets_dir else _default_puppets_dir()
    style_map = {style.character_id: style for style in styles}
    if not style_map:
        raise ValueError("render_dynamic_animation needs at least one SpeakerStyle")

    rigs: dict[str, PuppetRig] = {}
    for character_id in style_map:
        rigs[character_id] = PuppetRig(
            PuppetSkin.load_or_create(puppets_root / character_id)
        )

    analyzer = AudioAnalyzer(fps=fps, seed=seed)
    analyzed = analyzer.analyze(
        audio_path, list(turns), duration_override=duration_override, use_rhubarb=use_rhubarb
    )

    resolved_panorama = Path(panorama_path) if panorama_path else None
    if resolved_panorama is None and scene:
        if _SCENE_RESOLVER is None:
            raise RuntimeError(
                "scene resolution is not registered; pass panorama_path "
                "or import the product facade that registers one"
            )
        resolved_panorama = _SCENE_RESOLVER(scene)
    if stage_mode == "dual_presence":
        if enable_cta:
            _LOG.warning("dual-presence mode ignores the vertical terminal CTA")
        compositor = DualPresenceCompositor(
            rigs=rigs,
            styles=style_map,
            width=width,
            height=height,
            panorama_path=resolved_panorama,
        )
    else:
        compositor = ShotReverseShotCompositor(
            rigs=rigs,
            styles=style_map,
            width=width,
            height=height,
            enable_cta=enable_cta,
            outro_start_s=outro_start_s,
            outro_frame=outro_frame,
            panorama_path=resolved_panorama,
        )

    output_path = Path(output_path)
    subtitles_path: Path | None = None
    if burn_subtitles:
        subtitles_path = build_ass(
            list(turns),
            style_map,
            destination=output_path.with_suffix(".ass"),
            width=width,
            height=height,
            fade_out_s=subtitle_fade_s,
        )

    renderer = AnimationRenderer(width=width, height=height, fps=fps)
    return renderer.render(
        frame_iter=compositor.iter_frames(analyzed),
        audio_path=audio_path,
        output_path=output_path,
        duration_s=analyzed.duration_s,
        subtitles_path=subtitles_path,
    )


__all__ = [
    "SceneSpec",
    "load_scene",
    "register_scene_resolver",
    "render_dynamic_animation",
    "render_scene",
]
