# -*- coding: utf-8 -*-
"""One persona loader. Persona copy stays in each channel folder.

``load_persona(channel)`` reads ``channels_config/<channel>/master_dna.json``
and renders the shared prompt blocks. It does not import
``channels_config/<channel>/persona_dna.py`` and it does not contain that
file's sentences. A new channel is a new folder with ``master_dna.json``;
nothing in this module lists the channels.

The import-time facade that binds ``ACTIVE_PAGE`` is
``production.writers.persona_dna``.
"""
from __future__ import annotations
from shared.layer_bridge import Symbol

import ast
import json
import logging
from pathlib import Path

read_dna = Symbol('channels_config.shared.persona_bind', 'read_dna')
resolve_master_dna = Symbol('channels_config.shared.persona_bind', 'resolve_master_dna')
select_cta = Symbol('channels_config.shared.persona_bind', 'select_cta')
stub_dna = Symbol('channels_config.shared.persona_bind', 'stub_dna')

ENGINE_ROOT = Path(__file__).resolve().parents[2]
_LOG = logging.getLogger("production.writers.persona_dna")

# Folders that are not factory channel personas.
_SKIP_CHANNEL_DIRS = frozenset({"aiwake", "shared"})


class Persona:
    """Shared prompt blocks filled from one channel's DNA file."""

    def __init__(self, channel: str, dna: dict) -> None:
        self.channel = channel
        self.dna = dna
        phys = dna.get("Physical_Appearance", {})
        voice = dna.get("Brand_Voice_&_Mission", {})
        phil = dna.get("Philosophical_Core", {})
        batch = dna.get("Batch_Research", {})
        cta = dna.get("Call_To_Action_Inventory", {})

        self.display_name: str = dna.get("Subject", "Anna")
        self.age_years: int = phys.get("Age", 0)
        self.physical: str = (
            f"{self.age_years}-year-old: "
            f"{phys.get('Hair', '')}. "
            f"Physique: {phys.get('Physique', '')}. "
            f"Skin: {phys.get('Skin_Texture', '')} "
            f"Visual standard: {phys.get('Visual_Standard', '')}"
        )
        self.voice_principles: str = (
            f"Tone: {voice.get('Tone', '')}. "
            f"Mission: {voice.get('Mission_Statement', '')}. "
            f"Themes: {voice.get('Themes', '')}."
        )
        self.expertise_lens: str = (
            f"Target audience: {voice.get('Target_Audience', '')}. "
            + voice.get("Voice_Persona", "")
        )
        self.core_directive: str = dna.get("Core_Directive", "")
        self.core_mechanics: list[str] = dna.get("Core_Natural_Mechanics", [])
        self.philosophy_core_belief: str = phil.get("Core_Belief", "")
        self.philosophy_the_conflict: str = phil.get("The_Conflict", "")
        self.philosophy_science_metaphors: list[str] = phil.get("Science_Metaphors", [])
        self.philosophy_voice_adjectives: list[str] = phil.get("Voice_Adjectives", [])
        self.philosophy_banned_names: list[str] = phil.get("Banned_Name_References", [])
        self.narrative_angles: list[dict] = dna.get("Narrative_Angles", [])
        self.batch_default_size: int = batch.get("Default_Batch_Size", 20)
        self.batch_delimiter_open: str = batch.get("Delimiter_Open", "===NARRATIVE_")
        self.batch_delimiter_close: str = batch.get("Delimiter_Close", "===")
        self.batch_words_per_narrative: str = batch.get("Words_Per_Narrative", "150-250")
        self.batch_rotation_pattern: str = batch.get("Rotation_Pattern", "")
        self.cta_keywords: list[str] = cta.get("Authorized_Keywords", [])
        self.theme_map: dict[str, str] = cta.get("Theme_Keyword_Map", {})
        self.cta_voice_instruction: str = cta.get("CTA_Voice_Instruction", "")
        self.environments: list = dna.get("Environments", [])
        self.camera_angles: list[str] = dna.get("Camera_Angles", [])
        self.times_of_day: list[str] = dna.get("Times_Of_Day", [])
        self.actions: list[str] = dna.get("Anna_Actions", [])
        self.legacy_rule: dict = dna.get("Legacy_Rule", {"Probability": 0.0})

    def contextual_cta_keyword(self, topic: str) -> str:
        return select_cta(self.theme_map, self.cta_keywords, topic, "RESTORE")

    def random_cta_keyword(self) -> str:
        return select_cta({}, self.cta_keywords, "", "RESTORE")

    def philosophy_block(self) -> str:
        metaphors = "\n".join(f"  - {m}" for m in self.philosophy_science_metaphors)
        angles = "\n".join(
            f"  - {a.get('code', '')}: {a.get('description', '')}"
            for a in self.narrative_angles
        )
        banned = ", ".join(f'"{n}"' for n in self.philosophy_banned_names) or "none"
        return (
            f"PHILOSOPHY CORE:\n"
            f"  Belief: {self.philosophy_core_belief}\n"
            f"  The Conflict: {self.philosophy_the_conflict}\n\n"
            f"SCIENCE / SYSTEM METAPHORS (use when relevant):\n{metaphors}\n\n"
            f"NARRATIVE ANGLES AVAILABLE:\n{angles}\n\n"
            f"BANNED NAME REFERENCES (never appear in any output): {banned}"
        )

    def persona_context_block(self) -> str:
        mechanics = "\n".join(f"  - {m}" for m in self.core_mechanics)
        return (
            f"Identity: {self.display_name}, age {self.age_years}.\n"
            f"Core directive: {self.core_directive}\n"
            f"Physical: {self.physical}\n"
            f"Audience lens: {self.expertise_lens}\n"
            f"Voice: {self.voice_principles}\n"
            f"Core mechanics:\n{mechanics}\n\n"
            f"{self.philosophy_block()}"
        )

    def visual_style_block(self) -> str:
        env = self.dna.get("Visual_Environment_&_Composition", {})
        phys = self.dna.get("Physical_Appearance", {})
        env_names = [entry["name"] for entry in self.environments]
        lines = [
            f"Subject: {self.display_name}, age {self.age_years}. {phys.get('Hair', '')}.",
            f"Physique: {phys.get('Physique', '')}.",
            f"Skin realism: {phys.get('Skin_Texture', '')}",
            f"Visual standard: {phys.get('Visual_Standard', '')}",
            f"Atmosphere: {env.get('Atmosphere', 'Dramatic, atmospheric, high-fidelity.')}",
            f"Available environments: {'; '.join(env_names)}",
        ]
        return "\n".join(lines)


def load_persona(channel: str, *, root: Path | None = None) -> Persona:
    """Load ``channel`` from its own folder. No registry edit is required."""
    root = root or ENGINE_ROOT
    path = resolve_master_dna(channel, root)
    if path is None:
        candidate = root / "channels_config" / channel / "master_dna.json"
        _LOG.warning(
            "persona_dna: master_dna.json not found for page '%s' at %s. "
            "Using minimal stub DNA — reference-based pipeline will still function. "
            "Create channels_config/%s/master_dna.json to enable full LLM persona features.",
            channel,
            candidate,
            channel,
        )
        dna = stub_dna(channel)
    else:
        dna = read_dna(path)
    return Persona(channel, dna)


def _assigned_string(path: Path, name: str) -> str:
    if not path.is_file():
        return ""
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value = node.value
        else:
            continue
        for target in targets:
            if (
                isinstance(target, ast.Name)
                and target.id == name
                and isinstance(value, ast.Constant)
                and isinstance(value.value, str)
            ):
                return value.value
    return ""


def _dna_subject(path: Path) -> str:
    try:
        dna = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return path.parent.name
    persona = dna.get("persona") if isinstance(dna.get("persona"), dict) else {}
    return str(dna.get("Subject") or persona.get("name") or path.parent.name)


def channel_profile_rows(root: Path | None = None) -> list[dict[str, str]]:
    """One row per channel folder that carries its own ``master_dna.json``.

    Display name, default format, and avatar mode are read from that folder's
    ``page_config.py``. Aiwake is a product and is not a row. Adding a channel
    is adding a folder; this list is not maintained by hand.
    """
    root = root or ENGINE_ROOT
    base = root / "channels_config"
    rows: list[dict[str, str]] = []
    if not base.is_dir():
        return rows
    for folder in sorted(path for path in base.iterdir() if path.is_dir()):
        name = folder.name
        if name in _SKIP_CHANNEL_DIRS or name.startswith((".", "_", "test")):
            continue
        dna_path = folder / "master_dna.json"
        if not dna_path.is_file():
            continue
        page = folder / "page_config.py"
        rows.append(
            {
                "channel": name,
                "display_name": _assigned_string(page, "PAGE_DISPLAY_NAME") or _dna_subject(dna_path),
                "default_format": _assigned_string(page, "DEFAULT_FORMAT"),
                "avatar_mode": _assigned_string(page, "DEFAULT_AVATAR_MODE"),
                "dna": dna_path.relative_to(root).as_posix(),
            }
        )
    return rows
