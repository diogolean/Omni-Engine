# -*- coding: utf-8 -*-
"""Generic persona mechanics. No channel prompt copy lives here.

Each channel keeps its sentences in ``channels_config/<channel>/persona_dna.py``
and its DNA in ``master_dna.json`` beside that file. This module only reads the
JSON and fills the shared field names. ``production.writers.persona.load_persona``
is the loader the engine calls.
"""
from __future__ import annotations

import json
import random
from pathlib import Path
from types import SimpleNamespace


def read_dna(path: Path) -> dict:
    """Read one channel's ``master_dna.json``. A BOM is ignored."""
    return json.loads(path.read_text(encoding="utf-8-sig"))


def resolve_master_dna(channel: str, root: Path) -> Path | None:
    """Find DNA for ``channel`` under ``root``.

    Order: ``channels_config/<channel>/master_dna.json``, then the legacy
    ``pages_config/`` folder, then ``agents.media/master_dna.json`` when
    the channel sets ``LEGACY_MEDIA_DNA`` (that path is returned even when
    the file is absent, matching the previous import-time loader). Any
    other missing channel returns None so the caller can use the stub.
    """
    candidate = root / "channels_config" / channel / "master_dna.json"
    legacy = root / "pages_config" / channel / "master_dna.json"
    if candidate.is_file():
        return candidate
    if legacy.is_file():
        return legacy
    from shared.channel_flags import flag

    if flag(channel, "LEGACY_MEDIA_DNA"):
        return root / "agents.media" / "master_dna.json"
    return None


def stub_dna(channel: str) -> dict:
    """Minimal DNA used when a channel folder has no ``master_dna.json``."""
    label = channel.replace("_", " ").title()
    return {
        "Subject": label,
        "display_name": label,
        "Physical_Appearance": {},
        "Brand_Voice_&_Mission": {
            "Tone": "warm, engaging, authentic",
            "Mission_Statement": f"Create high-quality content for the {label} channel.",
            "Themes": "",
            "Target_Audience": "Social media audience",
            "Voice_Persona": f"The {label} voice.",
        },
        "Core_Directive": f"Produce compelling content for {label}.",
        "Core_Natural_Mechanics": [],
        "Philosophical_Core": {
            "Core_Belief": "",
            "The_Conflict": "",
            "Science_Metaphors": [],
            "Voice_Adjectives": ["warm", "authentic", "engaging"],
            "Banned_Name_References": [],
        },
        "Narrative_Angles": [],
        "Batch_Research": {
            "Default_Batch_Size": 10,
            "Delimiter_Open": "===TOPIC_",
            "Delimiter_Close": "===",
            "Words_Per_Narrative": "150-220",
            "Rotation_Pattern": "",
        },
        "Call_To_Action_Inventory": {
            "Authorized_Keywords": ["save", "share", "follow", "comment"],
            "Theme_Keyword_Map": {},
            "CTA_Voice_Instruction": "Invite the audience to engage warmly.",
        },
        "Environments": [],
        "Camera_Angles": [],
        "Times_Of_Day": [],
        "Anna_Actions": [],
        "Legacy_Rule": {"Probability": 0.0},
    }


def random_cta(keywords: list[str], fallback: str) -> str:
    return random.choice(keywords) if keywords else fallback


def select_cta(
    theme_map: dict[str, str],
    keywords: list[str],
    topic: str,
    fallback: str,
) -> str:
    """Longest Theme_Keyword_Map key that occurs in ``topic``, else a random keyword."""
    topic_lower = topic.lower()
    for fragment in sorted(theme_map.keys(), key=len, reverse=True):
        if fragment in topic_lower:
            return theme_map[fragment]
    return random_cta(keywords, fallback)


def bind_dna(
    dna: dict,
    *,
    display_name: str,
    age_years: int,
    physical: str,
    voice: str,
    lens: str,
    cta_fallback: str,
    cta_voice_default: str = "",
    mechanics: list[str] | None = None,
    actions: list[str] | None = None,
    legacy_default: dict | None = None,
    legacy: dict | None = None,
    batch_size_default: int = 20,
    batch_open_default: str = "===NARRATIVE_",
    batch_close_default: str = "===",
    batch_words_default: str = "150-250",
) -> SimpleNamespace:
    """Bind the shared field names. Prompt sentences are arguments, not stored here."""
    phil = dna.get("Philosophical_Core", {})
    batch = dna.get("Batch_Research", {})
    cta = dna.get("Call_To_Action_Inventory", {})
    if legacy is None:
        legacy = dna.get(
            "Legacy_Rule",
            {"Probability": 0.0} if legacy_default is None else legacy_default,
        )
    return SimpleNamespace(
        raw=dna,
        display_name=display_name,
        age_years=age_years,
        physical=physical,
        voice=voice,
        lens=lens,
        directive=dna.get("Core_Directive", ""),
        mechanics=dna.get("Core_Natural_Mechanics", []) if mechanics is None else mechanics,
        belief=phil.get("Core_Belief", ""),
        conflict=phil.get("The_Conflict", ""),
        metaphors=phil.get("Science_Metaphors", []),
        adjectives=phil.get("Voice_Adjectives", []),
        banned=phil.get("Banned_Name_References", []),
        angles=dna.get("Narrative_Angles", []),
        batch_size=batch.get("Default_Batch_Size", batch_size_default),
        batch_open=batch.get("Delimiter_Open", batch_open_default),
        batch_close=batch.get("Delimiter_Close", batch_close_default),
        batch_words=batch.get("Words_Per_Narrative", batch_words_default),
        batch_rotation=batch.get("Rotation_Pattern", ""),
        cta_keywords=cta.get("Authorized_Keywords", []),
        theme_map=cta.get("Theme_Keyword_Map", {}),
        cta_voice=cta.get("CTA_Voice_Instruction", cta_voice_default),
        cta_fallback=cta_fallback,
        environments=dna.get("Environments", []),
        camera_angles=dna.get("Camera_Angles", []),
        times_of_day=dna.get("Times_Of_Day", []),
        actions=dna.get("Anna_Actions", []) if actions is None else actions,
        legacy=legacy,
    )
