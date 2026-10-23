# -*- coding: utf-8 -*-
"""ACTIVE_PAGE facade over :func:`production.writers.persona.load_persona`.

Downstream modules keep importing these names. The values are bound once, from
the DNA file of ``ACTIVE_PAGE`` (default ``anna_protocol``), the same way the
previous loader bound them at import. Channel prompt copy is not stored here.
"""
from __future__ import annotations

import os

from production.writers.persona import ENGINE_ROOT as _ENGINE_ROOT
from production.writers.persona import load_persona

_ACTIVE_PAGE = os.environ.get("ACTIVE_PAGE", "anna_protocol")
_persona = load_persona(_ACTIVE_PAGE)

ANNA_DISPLAY_NAME: str = _persona.display_name
ANNA_AGE_YEARS: int = _persona.age_years
ANNA_PHYSICAL: str = _persona.physical
ANNA_VOICE_PRINCIPLES: str = _persona.voice_principles
ANNA_EXPERTISE_LENS: str = _persona.expertise_lens
CORE_DIRECTIVE: str = _persona.core_directive
CORE_MECHANICS: list[str] = _persona.core_mechanics
PHILOSOPHY_CORE_BELIEF: str = _persona.philosophy_core_belief
PHILOSOPHY_THE_CONFLICT: str = _persona.philosophy_the_conflict
PHILOSOPHY_SCIENCE_METAPHORS: list[str] = _persona.philosophy_science_metaphors
PHILOSOPHY_VOICE_ADJECTIVES: list[str] = _persona.philosophy_voice_adjectives
PHILOSOPHY_BANNED_NAMES: list[str] = _persona.philosophy_banned_names
NARRATIVE_ANGLES: list[dict] = _persona.narrative_angles
BATCH_DEFAULT_SIZE: int = _persona.batch_default_size
BATCH_DELIMITER_OPEN: str = _persona.batch_delimiter_open
BATCH_DELIMITER_CLOSE: str = _persona.batch_delimiter_close
BATCH_WORDS_PER_NARRATIVE: str = _persona.batch_words_per_narrative
BATCH_ROTATION_PATTERN: str = _persona.batch_rotation_pattern
CTA_KEYWORDS: list[str] = _persona.cta_keywords
CTA_VOICE_INSTRUCTION: str = _persona.cta_voice_instruction
ENVIRONMENTS: list = _persona.environments
CAMERA_ANGLES: list[str] = _persona.camera_angles
TIMES_OF_DAY: list[str] = _persona.times_of_day
ANNA_ACTIONS: list[str] = _persona.actions
LEGACY_RULE: dict = _persona.legacy_rule


def contextual_cta_keyword(topic: str) -> str:
    return _persona.contextual_cta_keyword(topic)


def random_cta_keyword() -> str:
    return _persona.random_cta_keyword()


def philosophy_block() -> str:
    return _persona.philosophy_block()


def persona_context_block() -> str:
    return _persona.persona_context_block()


def visual_style_block() -> str:
    return _persona.visual_style_block()
