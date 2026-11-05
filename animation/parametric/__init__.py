"""Parametric puppet engine.

Product shows, voices, and harnesses do not live here. Render through
:mod:`animation.api`.
"""
from __future__ import annotations

import importlib

__all__ = [
    "AnalyzedAudio",
    "AnimationRenderer",
    "AudioAnalyzer",
    "DialogueTurn",
    "DualPresenceCompositor",
    "PuppetAnchors",
    "PuppetRig",
    "PuppetSkin",
    "PuppetTheme",
    "RenderStats",
    "SceneSpec",
    "ShotReverseShotCompositor",
    "SpeakerStyle",
    "WordTiming",
    "load_scene",
    "render_dynamic_animation",
    "render_scene",
]

_EXPORTS = {
    "AnalyzedAudio": "animation.parametric.animator_types",
    "DialogueTurn": "animation.parametric.animator_types",
    "PuppetAnchors": "animation.parametric.animator_types",
    "PuppetTheme": "animation.parametric.animator_types",
    "SpeakerStyle": "animation.parametric.animator_types",
    "WordTiming": "animation.parametric.animator_types",
    "PuppetRig": "animation.parametric.puppet",
    "PuppetSkin": "animation.parametric.puppet",
    "AudioAnalyzer": "animation.parametric.audio_analyzer",
    "DualPresenceCompositor": "animation.parametric.compositor",
    "ShotReverseShotCompositor": "animation.parametric.compositor",
    "AnimationRenderer": "animation.parametric.renderer",
    "RenderStats": "animation.parametric.renderer",
    "SceneSpec": "animation.parametric.scene_spec",
    "load_scene": "animation.parametric.scene_spec",
    "render_dynamic_animation": "animation.api",
    "render_scene": "animation.parametric.scene_render",
}


def __getattr__(name: str):
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(module_name)
    return getattr(module, name)
