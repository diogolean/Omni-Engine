# -*- coding: utf-8 -*-
"""Turn one spoken beat into an object-first image prompt."""
from __future__ import annotations

import re

from modules.reel_grounding.brief import EpisodeBrief, build_episode_brief, visual_for_beat
from modules.reel_grounding.config import grounding_enabled
from modules.reel_grounding.memory import recall

_PRIMARY = re.compile(r"PRIMARY SUBJECT:\s*(.+?)(?:\.\s|EPISODE OBJECT:)", re.DOTALL)


def primary_subject(prompt: str) -> str:
    match = _PRIMARY.search(prompt or "")
    if not match:
        return ""
    return " ".join(match.group(1).split())[:400]


def _camera(kind: str, shot_text: str, suppress_wide: bool) -> str:
    text = (shot_text or "").strip()
    wide = suppress_wide and (
        not text
        or any(word in text.lower() for word in ("wide", "landscape", "horizon", "vista"))
    )
    if wide:
        return (
            "CAMERA: close or medium shot of the primary subject. "
            "A wide landscape is not the subject."
        )
    if text:
        return (
            f"CAMERA: {text} Apply this camera to the primary subject. "
            "Do not change the subject into a landscape, a ruin, or a city."
        )
    return "CAMERA: the primary subject fills most of the frame."


def _lighting(kind: str, lighting_text: str, suppress_fire: bool) -> str:
    text = (lighting_text or "").strip()
    fiery = suppress_fire and any(
        word in text.lower() for word in ("fire", "torch", "flame", "amber")
    )
    if fiery or (suppress_fire and kind in {"part", "lab"}):
        return (
            "LIGHTING: soft documentary light on the object, readable surface detail."
        )
    if text and kind != "site":
        return f"LIGHTING: {text} Keep the light on the primary subject."
    if text:
        return f"LIGHTING: {text}"
    return ""


def ground_image_prompt(
    *,
    topic: str,
    script: str = "",
    spoken: str = "",
    draft: str = "",
    shot_name: str = "",
    shot_text: str = "",
    lighting_name: str = "",
    lighting_text: str = "",
    channel: str = "",
    domain_anchors: str = "",
    memory_root=None,
) -> str:
    """Lead with the physical subject of this beat.

    ``draft`` is the old prompt (style block, subject-pool ruins line, or an
    audio-first scene). It is not copied through: those strings were putting
    megaliths and place names ahead of the artefact. Pass ``REEL_VISUAL_GROUNDING=0``
    to keep ``draft`` unchanged.
    """
    del shot_name, lighting_name  # names are for logs; the instruction text is enough
    if not grounding_enabled():
        return draft or spoken
    brief: EpisodeBrief = build_episode_brief(topic, script)
    visual = visual_for_beat(brief, spoken or topic)
    hints = recall(
        channel=channel,
        topic=brief.topic or topic,
        spoken=spoken,
        root=memory_root,
    )
    lines = [
        f"PRIMARY SUBJECT: {visual.subject}.",
        (
            f"EPISODE OBJECT: {brief.hero_short}. "
            "Every frame shows this same subject, not a substitute landmark."
        ),
    ]
    if (spoken or "").strip():
        lines.append(f'SPOKEN BEAT: "{spoken.strip()}".')
    anchors = (domain_anchors or "").strip()
    if anchors and anchors.lower() not in " ".join(lines).lower():
        lines.append(f"SETTING CONTEXT: {anchors}.")
    if visual.place_clause:
        lines.append(visual.place_clause)
    lines.append(_camera(visual.kind, shot_text, visual.suppress_wide))
    light = _lighting(visual.kind, lighting_text, visual.suppress_fire)
    if light:
        lines.append(light)
    if hints.accepted:
        lines.append(f"PREVIOUSLY ACCEPTED DEPICTION: {hints.accepted[0]}")
    if hints.rejected:
        lines.append(
            "PREVIOUSLY REJECTED, DO NOT REPEAT: " + "; ".join(hints.rejected)
        )
    if visual.avoid:
        lines.append("DO NOT DEPICT: " + "; ".join(visual.avoid) + ".")
    lines.append(
        "Photoreal documentary photograph, 35mm film grain, full-bleed, "
        "no text, no watermark, no border."
    )
    return " ".join(lines)
