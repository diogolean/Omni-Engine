# -*- coding: utf-8 -*-
"""Turn one spoken beat into an object-first image prompt."""
from __future__ import annotations

import re
from dataclasses import dataclass

from modules.reel_grounding.brief import (
    BeatVisual,
    EpisodeBrief,
    build_episode_brief,
    known_for,
    visual_for_beat,
)
from modules.reel_grounding.config import grounding_enabled
from modules.reel_grounding.memory import recall

_PRIMARY = re.compile(
    r"PRIMARY SUBJECT:\s*(.+?)\.\s*"
    r"(?:THIS BEAT'S OBJECT:|EPISODE OBJECT:|SPOKEN BEAT:)",
    re.DOTALL,
)
# Clauses that pull a small-object beat toward a landmark or a city.
_ARCH_CLAUSE = (
    "architecture", "monument", "marble", "temple", "ruins", "megalith",
    "pyramid", "ziggurat", "skyline", "clockwork", "clock", "vault",
    "cannon", "statue", "fog", "cyberpunk",
)


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
            "Keep the frame on that subject."
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


def _setting_context(anchors: str, *, small_object: bool) -> str:
    """Drop landmark clauses when this beat's picture is a small object."""
    text = (anchors or "").strip().strip(".")
    if not text:
        return ""
    if not small_object:
        return text
    kept: list[str] = []
    for clause in text.split(","):
        low = clause.lower()
        if any(word in low for word in _ARCH_CLAUSE):
            continue
        bit = clause.strip()
        if bit:
            kept.append(bit)
    return ", ".join(kept)


def _bare_label(label: str) -> str:
    text = " ".join((label or "").split())
    text = re.sub(r"^(?:the|a|an)\s+", "", text, flags=re.IGNORECASE).strip()
    if text.lower() in {"the", "a", "an"}:
        return ""
    return text


def _named_others(visual: BeatVisual) -> list[str]:
    kept: list[str] = []
    for label in visual.other_objects:
        bare = _bare_label(label)
        if not bare:
            continue
        shown = label if label.lower().startswith("the ") else f"the {bare}"
        if shown not in kept:
            kept.append(shown)
    return kept


def ban_conflicts(token: str, spoken: str, object_label: str) -> bool:
    """True when a channel negative would ban this beat's own subject."""
    low = " ".join((token or "").lower().split())
    if not low:
        return True
    blob = f"{spoken or ''} {object_label or ''}".lower()
    if low in blob:
        return True
    item = known_for(object_label)
    if item and low in item.allow_bans:
        return True
    if item and any(alias in (spoken or "").lower() for alias in item.aliases):
        if low in item.allow_bans:
            return True
    return False


def filter_banned_subjects(
    banned: list[str] | None,
    *,
    spoken: str = "",
    object_label: str = "",
) -> list[str]:
    kept: list[str] = []
    seen: set[str] = set()
    for token in banned or []:
        text = " ".join(str(token).split())
        key = text.lower()
        if not text or key in seen or ban_conflicts(text, spoken, object_label):
            continue
        seen.add(key)
        kept.append(text)
    return kept


def _drop_tokens(text: str, tokens: list[str]) -> str:
    cleaned = text or ""
    for token in tokens:
        if not token:
            continue
        cleaned = re.sub(
            rf"(?i)\b{re.escape(token)}\b",
            "",
            cleaned,
        )
    cleaned = re.sub(r"(?:\s*,\s*){2,}", ", ", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip(" ,.;")


def exclusion_terms(visual: BeatVisual) -> list[str]:
    """Words for the negative prompt. They stay out of the positive prompt."""
    terms: list[str] = []
    shown = (visual.object_label or "").lower()
    for item in visual.avoid:
        if "statue" in item and "statue" in shown:
            continue
        if item not in terms:
            terms.append(item)
    for label in _named_others(visual):
        if label not in terms:
            terms.append(label)
    return terms


@dataclass
class BeatPrompt:
    positive: str
    negative: str
    banned: list[str]
    object_label: str


def assemble_beat(
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
    previous_spoken: str = "",
    next_spoken: str = "",
    locked_object: str = "",
    banned: list[str] | None = None,
    negative: str = "",
) -> BeatPrompt:
    """Positive prompt, negative prompt, and the bans that still apply."""
    positive = ground_image_prompt(
        topic=topic,
        script=script,
        spoken=spoken,
        draft=draft,
        shot_name=shot_name,
        shot_text=shot_text,
        lighting_name=lighting_name,
        lighting_text=lighting_text,
        channel=channel,
        domain_anchors=domain_anchors,
        memory_root=memory_root,
        previous_spoken=previous_spoken,
        next_spoken=next_spoken,
        locked_object=locked_object,
    )
    brief = build_episode_brief(topic, script)
    visual = visual_for_beat(
        brief,
        spoken or topic,
        script=script,
        previous_spoken=previous_spoken,
        next_spoken=next_spoken,
        locked_object=locked_object,
    )
    label = visual.object_label
    kept = filter_banned_subjects(banned, spoken=spoken, object_label=label)
    dropped = [
        str(token) for token in (banned or [])
        if ban_conflicts(str(token), spoken, label)
    ]
    negative_text = _drop_tokens(negative or "", dropped)
    extras = exclusion_terms(visual)
    hints = recall(
        channel=channel,
        topic=brief.topic or topic,
        spoken=spoken,
        root=memory_root,
    )
    for item in hints.rejected:
        if item and item not in extras:
            extras.append(item)
    if extras:
        extra = ", ".join(extras)
        negative_text = f"{negative_text}, {extra}" if negative_text else extra
    return BeatPrompt(
        positive=positive,
        negative=negative_text,
        banned=kept,
        object_label=label,
    )


def format_resolved_objects(
    topic: str,
    script: str,
    beats: list[str],
    *,
    banned: list[str] | None = None,
) -> str:
    """One line per beat: index, resolved object, negatives, spoken text."""
    rows: list[str] = []
    for index, beat in enumerate(beats):
        previous = beats[index - 1] if index else ""
        following = beats[index + 1] if index + 1 < len(beats) else ""
        plan = assemble_beat(
            topic=topic,
            script=script,
            spoken=beat,
            previous_spoken=previous,
            next_spoken=following,
            banned=banned,
        )
        label = plan.object_label or "(none)"
        neg = ", ".join(plan.banned) if plan.banned else "-"
        rows.append(f"{index + 1:02d}\t{label}\tneg={neg}\t{beat}")
    return "\n".join(rows)


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
    previous_spoken: str = "",
    next_spoken: str = "",
    locked_object: str = "",
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
    visual = visual_for_beat(
        brief,
        spoken or topic,
        script=script,
        previous_spoken=previous_spoken,
        next_spoken=next_spoken,
        locked_object=locked_object,
    )
    hints = recall(
        channel=channel,
        topic=brief.topic or topic,
        spoken=spoken,
        root=memory_root,
    )
    lines = [f"PRIMARY SUBJECT: {visual.subject}."]
    if visual.object_label and _bare_label(visual.object_label):
        lines.append(
            f"THIS BEAT'S OBJECT: {visual.object_label}. "
            "This frame shows this object."
        )
    elif brief.hero_short:
        lines.append(
            f"EPISODE OBJECT: {brief.hero_short}. "
            "Every frame shows this same subject, not a substitute landmark."
        )
    if (spoken or "").strip():
        lines.append(f'SPOKEN BEAT: "{spoken.strip()}".')
    anchors = _setting_context(
        domain_anchors, small_object=bool(visual.object_label),
    )
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
    lines.append(
        "STYLE LOCK: photoreal documentary photograph from a real camera, "
        "natural materials and dust, 35mm film grain, full-bleed, "
        "no text, no watermark, no border."
    )
    return " ".join(lines)
