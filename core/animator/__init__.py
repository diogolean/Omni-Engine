# -*- coding: utf-8 -*-
"""Compatibility facade. The parametric engine lives in ``animation.parametric``.

Aiwake voices, puppet factories, arenas, and harness output stay in this
package until M10 (``products/aiwake/``). Importing this facade registers
the Aiwake scene resolver and the Aiwake seating plate.
"""
from __future__ import annotations

from animation.api import register_scene_resolver
from animation.api import render_dynamic_animation

from .animator_types import (
    AnalyzedAudio,
    DialogueTurn,
    PuppetAnchors,
    PuppetTheme,
    SpeakerStyle,
    WordTiming,
)
from .audio_analyzer import AudioAnalyzer
from .compositor import DualPresenceCompositor, ShotReverseShotCompositor
from .puppet import PuppetRig, PuppetSkin
from .renderer import AnimationRenderer, RenderStats


def _resolve_scene(scene: str):
    from .pipeline import resolve_scene_panorama

    return resolve_scene_panorama(scene)


register_scene_resolver(_resolve_scene)

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
    "ShotReverseShotCompositor",
    "SpeakerStyle",
    "WordTiming",
    "render_dynamic_animation",
]
