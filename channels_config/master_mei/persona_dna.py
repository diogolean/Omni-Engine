# -*- coding: utf-8 -*-
"""
Master Mei persona — sentences for this channel only.

DNA is ``master_dna.json`` in this folder. The generic loader is
``production.writers.persona.load_persona``.
"""
from __future__ import annotations

import random
from pathlib import Path

from channels_config.shared.persona_bind import bind_dna, random_cta, read_dna, select_cta

_P = read_dna(Path(__file__).resolve().parent / "master_dna.json")
_persona = _P.get("persona", {})
_phys = _P.get("Physical_Appearance", {})
_voice = _P.get("Brand_Voice_&_Mission", {})
_age = _P.get("Physical_Appearance", {}).get("Age", 72)
_name = _persona.get("name") or _P.get("Subject", "Master Mei")

_V = bind_dna(
    _P,
    display_name=_name,
    age_years=_age,
    physical=(
        f"{_age}-year-old East Asian sage Master Mei: "
        f"{_phys.get('Hair', 'long white hair in a traditional bun')}. "
        f"Beard: {_phys.get('Beard', 'long flowing white beard')}. "
        f"Wardrobe: {_phys.get('Wardrobe', 'traditional dark robes with gold accents')}. "
        f"Physique: {_phys.get('Physique', '')}. "
        f"Skin: {_phys.get('Skin_Texture', '')} "
        f"Visual standard: {_phys.get('Visual_Standard', '')}"
    ),
    voice=(
        f"Tone: {_voice.get('Tone', _persona.get('voice_tone', ''))}. "
        f"Mission: {_voice.get('Mission_Statement', _persona.get('core_mission', ''))}. "
        f"Themes: {_voice.get('Themes', '')}. "
        "Command over comfort. No hype. No therapy-speak. Captions are precise and uncompromising."
    ),
    lens=(
        f"Target audience: {_voice.get('Target_Audience', 'Men seeking liberation')}. "
        "Protocol for Liberation: Pai Mei cold discipline, financial warfare, "
        "traps of illusion (seduction), and classical strategy "
        "(Sun Tzu, Marcus Aurelius, Musashi, Buddhist detachment)."
    ),
    cta_fallback="DISCIPLINE",
    cta_voice_default=(
        "End with a firm command to master the mind or step into real discipline today."
    ),
    legacy_default={"Probability": 0.0},
    batch_words_default="120-200",
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


def contextual_cta_keyword(topic: str = "") -> str:
    return select_cta(_THEME_MAP, CTA_KEYWORDS, topic or "", "DISCIPLINE")


def random_cta_keyword() -> str:
    return random_cta(CTA_KEYWORDS, "DISCIPLINE")


def philosophy_block() -> str:
    metaphors = "\n".join(f"  - {m}" for m in PHILOSOPHY_SCIENCE_METAPHORS)
    angles = "\n".join(
        f"  - {a.get('code', '')}: {a.get('description', '')}"
        for a in NARRATIVE_ANGLES
    )
    banned = ", ".join(f'"{n}"' for n in PHILOSOPHY_BANNED_NAMES) or "none"
    pillars = _persona.get("content_pillars", [])
    pillar_str = "\n".join(f"  - {p}" for p in pillars)
    return (
        f"PHILOSOPHY CORE:\n"
        f"  Belief: {PHILOSOPHY_CORE_BELIEF}\n"
        f"  The Conflict: {PHILOSOPHY_THE_CONFLICT}\n\n"
        f"CONTENT PILLARS:\n{pillar_str}\n\n"
        f"SCIENCE METAPHORS (use when relevant):\n{metaphors}\n\n"
        f"NARRATIVE ANGLES AVAILABLE:\n{angles}\n\n"
        f"BANNED NAME REFERENCES: {banned}"
    )


def persona_context_block() -> str:
    """Compact persona context for LLM system prompts."""
    mechanics = "\n".join(f"  - {m}" for m in CORE_MECHANICS)
    return (
        f"CHANNEL: {ANNA_DISPLAY_NAME}\n"
        f"Identity: {ANNA_DISPLAY_NAME}, age {ANNA_AGE_YEARS} — "
        f"{_persona.get('archetype', 'Stoic Executive Mentor')}.\n"
        f"Core directive: {CORE_DIRECTIVE}\n"
        f"Physical: {ANNA_PHYSICAL}\n"
        f"Audience lens: {ANNA_EXPERTISE_LENS}\n"
        f"Voice: {ANNA_VOICE_PRINCIPLES}\n"
        f"Core mechanics:\n{mechanics}\n\n"
        f"{philosophy_block()}"
    )


def visual_style_block() -> str:
    env = _P.get("visual_identity", {})
    phys = _P.get("Physical_Appearance", {})
    env_names = [e["name"] for e in ENVIRONMENTS if isinstance(e, dict) and "name" in e]
    lines = [
        f"Subject: {ANNA_DISPLAY_NAME}, age {ANNA_AGE_YEARS}. {phys.get('Hair', '')}.",
        f"Physique: {phys.get('Physique', '')}.",
        f"Skin realism: {phys.get('Skin_Texture', '')}",
        f"Visual standard: {phys.get('Visual_Standard', '')}",
        f"Atmosphere: {env.get('mood', 'Dark, intense, stoic martial atmosphere.')}",
        f"Style: {env.get('style', 'cinematic 4K martial photography')}",
        f"Available environments: {'; '.join(env_names)}",
    ]
    return "\n".join(lines)


def random_narrative_angle() -> str:
    angles = _P.get("narrative_angles", [])
    if angles:
        return random.choice(angles)
    if NARRATIVE_ANGLES:
        return NARRATIVE_ANGLES[0].get("description", "Challenge soft modern habits.")
    return "Challenge soft modern habits with a warrior truth."


def random_environment() -> str:
    envs = _P.get("environments", [])
    if envs:
        return random.choice(envs)
    if ENVIRONMENTS:
        e0 = ENVIRONMENTS[0]
        return e0.get("desc", e0.get("name", "Dark training ground in the rain."))
    return "Dark martial courtyard under torchlight."
