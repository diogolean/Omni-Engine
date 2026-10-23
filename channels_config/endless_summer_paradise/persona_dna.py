# -*- coding: utf-8 -*-
"""
Endless Summer Paradise persona — sentences for this channel only.

Avatar-OFF library-ingest page. DNA is ``master_dna.json`` in this folder.
The generic loader is ``production.writers.persona.load_persona``.
Prefer ChannelFactory / EndlessSummerParadiseAdapter over importing this module.
"""
from __future__ import annotations

from pathlib import Path

from channels_config.shared.persona_bind import bind_dna, random_cta, read_dna, select_cta

_P = read_dna(Path(__file__).resolve().parent / "master_dna.json")
_voice = _P.get("Brand_Voice_&_Mission", {})

_V = bind_dna(
    _P,
    display_name=_P.get("Subject", "Endless Summer Paradise"),
    age_years=0,
    physical="Avatar-OFF mode: library ingest, no generated human subject.",
    voice=(
        f"Tone: {_voice.get('Tone', '')}. "
        f"Mission: {_voice.get('Mission_Statement', '')}. "
        f"Themes: {_voice.get('Themes', '')}."
    ),
    lens=(
        f"Target audience: {_voice.get('Target_Audience', '')}. "
        "Tropical aesthetic summer scenery curation."
    ),
    cta_fallback="SUMMER",
    mechanics=[],
    actions=[],
    legacy={"Probability": 0.0},
    batch_size_default=10,
    batch_words_default="80-140",
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
    return select_cta(_THEME_MAP, CTA_KEYWORDS, topic, "SUMMER")


def random_cta_keyword() -> str:
    return random_cta(CTA_KEYWORDS, "SUMMER")


def persona_context_block() -> str:
    return (
        f"CHANNEL: {ANNA_DISPLAY_NAME}\n"
        f"{CORE_DIRECTIVE}\n"
        f"{ANNA_VOICE_PRINCIPLES}\n"
        f"{ANNA_EXPERTISE_LENS}"
    ).strip()
