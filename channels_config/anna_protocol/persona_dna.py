# -*- coding: utf-8 -*-
"""
Anna Protocol persona — sentences for this channel only.

DNA is ``master_dna.json`` in this folder. The generic loader is
``production.writers.persona.load_persona``. Do not import this module from
the engine; the ACTIVE_PAGE facade reads the DNA file directly.
"""
from __future__ import annotations

from pathlib import Path

from channels_config.shared.persona_bind import bind_dna, random_cta, read_dna, select_cta

_P = read_dna(Path(__file__).resolve().parent / "master_dna.json")
_phys = _P.get("Physical_Appearance", {})
_voice = _P.get("Brand_Voice_&_Mission", {})
_age = _P.get("Physical_Appearance", {}).get("Age", 72)

_V = bind_dna(
    _P,
    display_name=_P.get("Subject", "Anna"),
    age_years=_age,
    physical=(
        f"{_age}-year-old holistic authority: "
        f"{_phys.get('Hair', 'natural silver hair')}. "
        f"Physique: {_phys.get('Physique', '')}. "
        f"Skin: {_phys.get('Skin_Texture', '')} "
        f"Visual standard: {_phys.get('Visual_Standard', '')}"
    ),
    voice=(
        f"Tone: {_voice.get('Tone', '')}. "
        f"Mission: {_voice.get('Mission_Statement', '')}. "
        f"Themes: {_voice.get('Themes', '')}. "
        "Never markets with AI-shaped hype words (no 'unlock', 'discover', 'game-changer'). "
        "Hooks are decisive and specific."
    ),
    lens=(
        f"Target audience: {_voice.get('Target_Audience', 'USA Premium Market')}. "
        "Translates biochemical mechanisms into disciplined, humane guidance for a "
        "premium longevity audience."
    ),
    cta_fallback="RESTORE",
    legacy_default={"Probability": 0.15},
    batch_size_default=20,
    batch_words_default="150-250",
)

ANNA_DISPLAY_NAME: str = _V.display_name
ANNA_AGE_YEARS: int = _V.age_years
ANNA_PHYSICAL: str = _V.physical
ANNA_VOICE_PRINCIPLES: str = _V.voice
ANNA_EXPERTISE_LENS: str = _V.lens
CORE_DIRECTIVE: str = _V.directive
CORE_MECHANICS: list[str] = _V.mechanics
PHILOSOPHY_CORE_BELIEF: str = _V.belief
PHILOSOPHY_THE_CONFLICT: str = _V.conflict
PHILOSOPHY_SCIENCE_METAPHORS: list[str] = _V.metaphors
PHILOSOPHY_VOICE_ADJECTIVES: list[str] = _V.adjectives
PHILOSOPHY_BANNED_NAMES: list[str] = _V.banned
NARRATIVE_ANGLES: list[dict] = _V.angles
BATCH_DEFAULT_SIZE: int = _V.batch_size
BATCH_DELIMITER_OPEN: str = _V.batch_open
BATCH_DELIMITER_CLOSE: str = _V.batch_close
BATCH_WORDS_PER_NARRATIVE: str = _V.batch_words
BATCH_ROTATION_PATTERN: str = _V.batch_rotation
CTA_KEYWORDS: list[str] = _V.cta_keywords
_THEME_MAP: dict[str, str] = _V.theme_map
CTA_VOICE_INSTRUCTION: str = _V.cta_voice
ENVIRONMENTS: list[dict] = _V.environments
CAMERA_ANGLES: list[str] = _V.camera_angles
TIMES_OF_DAY: list[str] = _V.times_of_day
ANNA_ACTIONS: list[str] = _V.actions
LEGACY_RULE: dict = _V.legacy


def contextual_cta_keyword(topic: str) -> str:
    return select_cta(_THEME_MAP, CTA_KEYWORDS, topic, "RESTORE")


def random_cta_keyword() -> str:
    return random_cta(CTA_KEYWORDS, "RESTORE")


def philosophy_block() -> str:
    metaphors = "\n".join(f"  - {m}" for m in PHILOSOPHY_SCIENCE_METAPHORS)
    angles = "\n".join(
        f"  - {a.get('code', '')}: {a.get('description', '')}"
        for a in NARRATIVE_ANGLES
    )
    banned = ", ".join(f'"{n}"' for n in PHILOSOPHY_BANNED_NAMES)
    return (
        f"PHILOSOPHY CORE:\n"
        f"  Belief: {PHILOSOPHY_CORE_BELIEF}\n"
        f"  The Conflict: {PHILOSOPHY_THE_CONFLICT}\n\n"
        f"SCIENCE METAPHORS (use when relevant):\n{metaphors}\n\n"
        f"NARRATIVE ANGLES AVAILABLE:\n{angles}\n\n"
        f"BANNED NAME REFERENCES (never appear in any output): {banned}"
    )


def persona_context_block() -> str:
    mechanics = "\n".join(f"  - {m}" for m in CORE_MECHANICS)
    return (
        f"Identity: {ANNA_DISPLAY_NAME}, age {ANNA_AGE_YEARS}.\n"
        f"Core directive: {CORE_DIRECTIVE}\n"
        f"Physical: {ANNA_PHYSICAL}\n"
        f"Audience lens: {ANNA_EXPERTISE_LENS}\n"
        f"Voice: {ANNA_VOICE_PRINCIPLES}\n"
        f"Core natural mechanics:\n{mechanics}\n\n"
        f"{philosophy_block()}"
    )


def visual_style_block() -> str:
    env = _P.get("Visual_Environment_&_Composition", {})
    phys = _P.get("Physical_Appearance", {})
    env_names = [e["name"] for e in ENVIRONMENTS]
    lines = [
        f"Subject: {ANNA_DISPLAY_NAME}, age {ANNA_AGE_YEARS}. {phys.get('Hair', '')}.",
        f"Physique: {phys.get('Physique', '')}.",
        f"Skin realism: {phys.get('Skin_Texture', '')}",
        f"Visual standard: {phys.get('Visual_Standard', '')}",
        f"Atmosphere: {env.get('Atmosphere', 'Dramatic, atmospheric, grounded in nature.')}",
        f"Available environments: {'; '.join(env_names)}",
    ]
    return "\n".join(lines)
