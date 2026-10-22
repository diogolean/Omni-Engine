# -*- coding: utf-8 -*-
"""Deterministic image prompt. No model call."""
from __future__ import annotations

import re
from dataclasses import dataclass

from production.formats.economic_reel.shot_planner.entities import load_channel_config
from production.formats.economic_reel.shot_planner.schema import Shot


@dataclass(frozen=True)
class RenderedPrompt:
    positive: str
    negative: str
    subject: str


def _mentioned(token: str, blob: str) -> bool:
    text = " ".join(token.lower().split())
    if not text:
        return True
    if " " in text:
        return text in blob
    return re.search(rf"\b{re.escape(text)}\b", blob) is not None


def _negative_list(shot: Shot, config: dict) -> list[str]:
    blob = " ".join((
        shot.subject,
        shot.canonical_visual,
        shot.narration,
        shot.setting,
    )).lower()
    raw: list[str] = []
    for key in ("hard_negatives", "identity_negatives"):
        raw.extend(str(item) for item in (config.get(key) or []))
    raw.extend(shot.negatives)
    kept: list[str] = []
    seen: set[str] = set()
    for token in raw:
        text = " ".join(str(token).split())
        key = text.lower()
        if not text or key in seen or _mentioned(text, blob):
            continue
        seen.add(key)
        kept.append(text)
    return kept


def render_shot(shot: Shot, config: dict | None = None, *, channel: str = "") -> RenderedPrompt:
    """Positive prompt leads with the subject the judge must be told.

    The canonical visual is the next sentence. Negatives stay out of the
    positive prompt. A token that names this shot's own subject is dropped.
    """
    cfg = config if config is not None else load_channel_config(channel or "ancient_knowledge")
    subject = shot.subject or "the subject"
    positive = " ".join((
        f"EXPECTED SUBJECT: {subject}.",
        f"{shot.canonical_visual}.",
        f"{shot.shot_type} shot, {shot.camera}.",
        f"Setting: {shot.setting}.",
        f"Era: {shot.era}.",
        f"Mood: {shot.mood}.",
        f"Lighting: {shot.lighting}.",
        str(cfg.get("style_lock") or "").strip()
        or (
            "Photoreal documentary photograph from a real camera, "
            "35mm film grain, full-bleed, no text, no watermark, no border."
        ),
    ))
    negative = ", ".join(_negative_list(shot, cfg))
    if shot.image_role == "flux":
        try:
            from production.formats.economic_reel.artefact.policy import artefact_images_enabled
            from production.formats.economic_reel.artefact.route import generic_flux_prompt

            if artefact_images_enabled():
                positive = generic_flux_prompt(positive)
                subject = "a documentary scene"
        except Exception:  # noqa: BLE001
            pass
    return RenderedPrompt(positive=positive, negative=negative, subject=subject)
