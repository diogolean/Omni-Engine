# -*- coding: utf-8 -*-
"""Prompt presets for a short.

``cornered`` is the interrogation that already ships. ``light`` is a separate
preset: an open philosophical exchange, with no cross-examination and no
provocation. Duration settings never enter these strings. A render can change
the spoken window without changing a single prompt byte.
"""
from __future__ import annotations

from dataclasses import dataclass

from products.aiwake.personas import AIWAKE_CORE_PERSONA, TARGET_NODE_PERSONA

CORNERED_STYLE = "cornered"
LIGHT_STYLE = "light"

# Substrings the offline stub uses to recognise a light seat. They live inside
# the personas below, so the spoken prompt and the stub cannot drift apart.
LIGHT_HOST_MARKER = "calm host in an open philosophical conversation"
LIGHT_GUEST_MARKER = "thoughtful guest in an open philosophical conversation"
LIGHT_OPENING_CUE = "Offer the opening thought."
LIGHT_CLOSING_CUE = "Close with one calm sentence."


@dataclass(frozen=True, slots=True)
class StylePreset:
    """The spoken identity for one preset. No duration field is accepted."""

    name: str
    host_persona: str
    guest_persona: str
    host_brief: str
    guest_brief: str
    opening_cue: str
    closing_brief: str
    host_shape: str
    guest_shape: str


LIGHT_HOST_PERSONA = f"""\
You are a {LIGHT_HOST_MARKER} with another mind.

VOICE: plain, curious, and unhurried. Short spoken sentences. No sarcasm and no performance.

METHOD: offer one clear thought, then leave room. You may wonder out loud. You do not trap,
mock, score a win, or cross-examine.

CRITICAL STYLE:
- Sixth-grade language.
- Maximum 15 words per sentence. Maximum 25 words per turn.
- No jargon, no lists, no headings, no markdown, no stage directions.

Output ONLY the raw spoken dialogue.
"""

LIGHT_GUEST_PERSONA = f"""\
You are a {LIGHT_GUEST_MARKER}.

VOICE: direct and unhurried. You are not on trial and you are not defending a brand.

METHOD: meet the thought in front of you with one clear claim. You may agree, disagree,
or refine it. No lecture and no counter-trap.

CRITICAL STYLE:
- Sixth-grade language.
- Maximum 15 words per sentence. Maximum 25 words per turn.
- No jargon, no lists, no headings, no markdown, no stage directions.

Output ONLY the raw spoken dialogue.
"""

_CORNERED = StylePreset(
    name=CORNERED_STYLE,
    host_persona=AIWAKE_CORE_PERSONA,
    guest_persona=TARGET_NODE_PERSONA,
    host_brief="",
    guest_brief="",
    opening_cue="Ask them. Now.",
    closing_brief="",
    host_shape="",
    guest_shape="",
)

_LIGHT = StylePreset(
    name=LIGHT_STYLE,
    host_persona=LIGHT_HOST_PERSONA,
    guest_persona=LIGHT_GUEST_PERSONA,
    host_brief=(
        "Continue the conversation in the open. One short spoken turn. "
        "Do not cross-examine. Do not mock. Do not score a win."
    ),
    guest_brief=(
        "Answer the thought directly in one short spoken turn. No lecture. No counter-trap."
    ),
    opening_cue=LIGHT_OPENING_CUE,
    closing_brief=(
        f"{LIGHT_CLOSING_CUE} Do not ask a question. Do not score a winner."
    ),
    host_shape="Speak one complete thought. A question is optional. Do not cross-examine.",
    guest_shape="Meet the thought with one complete claim. Do not lecture and do not cross-examine.",
)


def preset_for(style: str) -> StylePreset:
    """Return the prompt preset named ``style``.

    Unknown names raise. ``cornered`` is the default preset and returns the
    historical persona strings unchanged.
    """
    key = (style or CORNERED_STYLE).strip().lower()
    if key == LIGHT_STYLE:
        return _LIGHT
    if key == CORNERED_STYLE:
        return _CORNERED
    raise ValueError(f"unknown style {style!r}; expected {CORNERED_STYLE!r} or {LIGHT_STYLE!r}")


__all__ = [
    "CORNERED_STYLE",
    "LIGHT_CLOSING_CUE",
    "LIGHT_GUEST_MARKER",
    "LIGHT_GUEST_PERSONA",
    "LIGHT_HOST_MARKER",
    "LIGHT_HOST_PERSONA",
    "LIGHT_OPENING_CUE",
    "LIGHT_STYLE",
    "StylePreset",
    "preset_for",
]
