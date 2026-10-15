# -*- coding: utf-8 -*-
"""Prompt templates, identity rules, and garment checks.

The positive prompt always starts with the avatar trigger. Age, eye, and
hair descriptors are rejected because they drift the LoRA identity. Every
scene must name a top garment and a bottom garment.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from models_avatar.avatar_config import AvatarConfig
from models_avatar.errors import AvatarConfigError, PromptPolicyError
from models_avatar.safety import merge_negatives

SLOT_NAMES: tuple[str, ...] = (
    "outfit",
    "setting",
    "pose",
    "expression",
    "lighting",
    "framing",
)

# Longer phrases first so "bikini top" wins over a shorter token.
_TOP_GARMENTS: tuple[str, ...] = (
    "bikini top",
    "sports bra",
    "bralette",
    "tank top",
    "crop top",
    "t-shirt",
    "tee",
    "button-up shirt",
    "shirt",
    "blouse",
    "sweater",
    "cardigan",
    "camisole",
    "jacket",
    "blazer",
    "hoodie",
    "knit top",
    "bra",
    "top",
)
_BOTTOM_GARMENTS: tuple[str, ...] = (
    "bikini bottoms",
    "athletic shorts",
    "running shorts",
    "biker shorts",
    "high-waisted shorts",
    "panties",
    "underwear",
    "jeans",
    "trousers",
    "pants",
    "skirt",
    "shorts",
    "leggings",
    "briefs",
    "thong",
)

_IDENTITY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("freckles", re.compile(r"freckles", re.IGNORECASE)),
    ("age", re.compile(r"\b\d{1,2}\s*-?\s*years?\s*old\b", re.IGNORECASE)),
    ("age", re.compile(r"\b\d{1,2}\s*yo\b", re.IGNORECASE)),
    (
        "minor",
        re.compile(
            r"\b(child|children|toddler|infant|preteen|underage|teen|teenager|loli|minor)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "eye color",
        re.compile(
            r"\b(blue|green|brown|hazel|gray|grey|amber)\s+eyes\b",
            re.IGNORECASE,
        ),
    ),
    (
        "hair",
        re.compile(
            r"\b(blonde|blond|brunette|redhead)\b|\bginger\s+hair\b",
            re.IGNORECASE,
        ),
    ),
    (
        "hair",
        re.compile(r"\b(black|brown|dark|auburn)\s+hair\b", re.IGNORECASE),
    ),
)

_NUDITY_PATTERN = re.compile(
    r"\b(nude|naked|topless|bottomless|nipples?|areola|genitals?|nsfw|explicit|porn)\b"
    r"|\b(no|without)\s+(clothes|clothing|a\s+top|a\s+shirt)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PromptTemplate:
    id: str
    set_name: str
    outfit: str
    setting: str
    pose: str
    expression: str
    lighting: str
    framing: str


@dataclass(frozen=True)
class TemplateLibrary:
    path: Path
    sentence: str
    sets: dict[str, tuple[PromptTemplate, ...]]

    def set_names(self) -> list[str]:
        return sorted(self.sets)

    def templates_for(self, set_name: str | None) -> tuple[PromptTemplate, ...]:
        if not set_name or set_name in {"all", "*"}:
            ordered: list[PromptTemplate] = []
            for name in sorted(self.sets):
                ordered.extend(self.sets[name])
            return tuple(ordered)
        try:
            return self.sets[set_name]
        except KeyError as exc:
            known = ", ".join(self.set_names()) or "(none)"
            raise AvatarConfigError(
                f"Unknown template set {set_name!r}. Known: {known}."
            ) from exc

    def get(self, set_name: str, template_id: str) -> PromptTemplate:
        for item in self.templates_for(set_name):
            if item.id == template_id:
                return item
        raise AvatarConfigError(
            f"Template {template_id!r} is not in set {set_name!r}."
        )


def default_template_path(avatar: AvatarConfig) -> Path:
    return avatar.directory / "prompts" / "templates.yaml"


def load_templates(path: Path) -> TemplateLibrary:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise AvatarConfigError(f"Cannot read templates {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise AvatarConfigError(f"Invalid template YAML {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise AvatarConfigError(f"Template file {path} must be a mapping.")
    sentence = str(raw.get("sentence") or "").strip()
    if not sentence:
        raise AvatarConfigError(f"Template file {path} needs a sentence.")
    missing_slots = [slot for slot in SLOT_NAMES if "{" + slot + "}" not in sentence]
    if missing_slots:
        raise AvatarConfigError(
            f"Template sentence in {path} is missing slots: {', '.join(missing_slots)}."
        )
    sets_raw = raw.get("sets") or {}
    if not isinstance(sets_raw, dict) or not sets_raw:
        raise AvatarConfigError(f"Template file {path} needs sets.")
    sets: dict[str, tuple[PromptTemplate, ...]] = {}
    for set_name, spec in sets_raw.items():
        if not isinstance(spec, dict):
            raise AvatarConfigError(f"Template set {set_name!r} must be a mapping.")
        items = spec.get("templates") or []
        if not isinstance(items, list) or not items:
            raise AvatarConfigError(f"Template set {set_name!r} needs templates.")
        parsed = tuple(_parse_template(str(set_name), item) for item in items)
        sets[str(set_name)] = parsed
    return TemplateLibrary(path=path, sentence=sentence, sets=sets)


def render_scene(library: TemplateLibrary, template: PromptTemplate) -> str:
    values = {slot: getattr(template, slot) for slot in SLOT_NAMES}
    try:
        return library.sentence.format(**values).strip()
    except KeyError as exc:
        raise PromptPolicyError(f"Template sentence has an unknown slot: {exc}") from exc


def compose_positive(avatar: AvatarConfig, scene: str) -> str:
    """Prefix the trigger, append the avatar quality suffix, then enforce policy."""
    trigger = avatar.identity.trigger.strip().strip(",")
    body = _strip_leading_trigger(scene, trigger)
    if not body:
        raise PromptPolicyError("Prompt scene is empty.")
    parts = [trigger, body]
    suffix = avatar.quality_suffix.strip().strip(",")
    if suffix:
        parts.append(suffix)
    text = ", ".join(part.strip().strip(",") for part in parts if part.strip())
    text = re.sub(r"\s+", " ", text).strip()
    enforce_positive(avatar, text)
    return text


def compose_negative(avatar: AvatarConfig, extra: str | None = None) -> str:
    """Avatar negatives plus any template extras plus the mandatory safety list."""
    return merge_negatives(avatar.negatives, extra)


def enforce_positive(avatar: AvatarConfig, text: str) -> None:
    """Reject identity drift, nudity, and unnamed garments.

    Built-in rules always run. ``extra_forbid_tokens`` can only add phrases.
    """
    trigger = avatar.identity.trigger.strip()
    if not text.casefold().startswith(trigger.casefold()):
        raise PromptPolicyError(
            f"Positive prompt must start with {trigger!r}."
        )
    lowered = text.casefold()
    for label, pattern in _IDENTITY_PATTERNS:
        match = pattern.search(text)
        if match:
            raise PromptPolicyError(
                f"Positive prompt contains a forbidden {label} descriptor {match.group(0)!r}. "
                "Identity prompts stay on the trigger word."
            )
    for token in avatar.extra_forbid_tokens:
        if token.casefold() in lowered:
            raise PromptPolicyError(
                f"Positive prompt contains avatar-forbidden token {token!r}."
            )
    nudity = _NUDITY_PATTERN.search(text)
    if nudity:
        raise PromptPolicyError(
            f"Positive prompt contains non-permitted wording {nudity.group(0)!r}. "
            "Name a top garment and a bottom garment instead."
        )
    _require_garments(text)


def _strip_leading_trigger(scene: str, trigger: str) -> str:
    body = (scene or "").strip().strip(",")
    pattern = re.compile(rf"^{re.escape(trigger)}\s*,?\s*", re.IGNORECASE)
    return pattern.sub("", body).strip().strip(",")


def _require_garments(text: str) -> None:
    top = _first_phrase(text, _TOP_GARMENTS)
    bottom = _first_phrase(text, _BOTTOM_GARMENTS)
    missing: list[str] = []
    if not top:
        missing.append("a top (shirt, blouse, sweater, bra, bikini top, sports bra, ...)")
    if not bottom:
        missing.append("a bottom (jeans, trousers, skirt, shorts, panties, bikini bottoms, ...)")
    if missing:
        raise PromptPolicyError(
            "Prompt must name both garments. Missing " + " and ".join(missing) + "."
        )


def _first_phrase(text: str, phrases: tuple[str, ...]) -> str | None:
    for phrase in phrases:
        if re.search(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", text, re.IGNORECASE):
            return phrase
    return None


def _parse_template(set_name: str, raw: Any) -> PromptTemplate:
    if not isinstance(raw, dict):
        raise AvatarConfigError(f"Template in {set_name!r} must be a mapping.")
    template_id = str(raw.get("id") or "").strip()
    if not template_id:
        raise AvatarConfigError(f"A template in set {set_name!r} is missing id.")
    values: dict[str, str] = {}
    for slot in SLOT_NAMES:
        text = str(raw.get(slot) or "").strip()
        if not text:
            raise AvatarConfigError(
                f"Template {set_name}/{template_id} is missing slot {slot!r}."
            )
        values[slot] = text
    return PromptTemplate(id=template_id, set_name=set_name, **values)
