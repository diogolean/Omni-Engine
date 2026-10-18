# -*- coding: utf-8 -*-
"""One narration-to-shot check, before any image is generated.

The judge scores a Flux still against the planned shot. This module is
the check that the planned shot is a fair picture for the spoken line:
an allowed scene, and no named artefact in a generated frame. An optional
text model runs once per episode when a key is present. Tests stay offline.
"""
from __future__ import annotations

import logging
import os

from modules.reel_artefact.flux_policy import acceptable_flux_concept, scene_fields
from modules.shot_planner.schema import SHOT_TYPES, Shot, ShotList

_LOG = logging.getLogger(__name__)


def review_plan(plan: ShotList, *, ask_llm: bool = True) -> ShotList:
    """Repair a flux shot that names an object, then optionally ask a model.

    The reel planner already spent one Gemini call on the shot list. It
    passes ``ask_llm=False`` so the check does not make a second call.
    """
    shots = [_repair(shot, index) for index, shot in enumerate(plan.shots, start=1)]
    reviewed = ShotList(
        channel=plan.channel,
        topic=plan.topic,
        narration=plan.narration,
        shots=shots,
        angle=plan.angle,
        source=dict(plan.source),
    )
    notes = _notes(reviewed)
    if notes:
        _LOG.info("shot plan check | %s", "; ".join(notes))
    else:
        _LOG.info("shot plan check | %d shots fit", len(reviewed.shots))
    if ask_llm and _llm_enabled():
        _ask_once(reviewed)
    return reviewed


def _repair(shot: Shot, index: int) -> Shot:
    if shot.image_role != "flux":
        return shot
    blob = " ".join((shot.subject, shot.canonical_visual, shot.camera))
    if acceptable_flux_concept(blob):
        return shot
    fields = scene_fields(shot.narration, index=index, event=shot.scene_type == "sea")
    shot_type = fields["shot_type"] if fields["shot_type"] in SHOT_TYPES else "wide"
    _LOG.info(
        "shot plan repair | %s | forbidden object in a flux shot",
        shot.id or index,
    )
    return Shot(
        id=shot.id,
        narration=shot.narration,
        entity_id=shot.entity_id,
        subject=fields["subject"],
        canonical_visual=fields["canonical_visual"],
        shot_type=shot_type,
        camera=fields["camera"],
        setting=fields["setting"],
        era=shot.era,
        mood=shot.mood,
        lighting=fields["lighting"],
        negatives=list(shot.negatives),
        kb_miss=shot.kb_miss,
        canonical_shape="",
        image_role="flux",
        scene_type=fields.get("scene") or shot.scene_type,
    )


def _notes(plan: ShotList) -> list[str]:
    found: list[str] = []
    for shot in plan.shots:
        if shot.image_role != "flux":
            continue
        blob = " ".join((shot.subject, shot.canonical_visual))
        if not acceptable_flux_concept(blob):
            found.append(f"{shot.id} still names an object")
    return found


def _llm_enabled() -> bool:
    if os.getenv("PYTEST_CURRENT_TEST"):
        return False
    flag = (os.getenv("REEL_PLAN_CHECK") or "auto").strip().lower()
    if flag in {"0", "off", "false", "no"}:
        return False
    return bool((os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip())


def _ask_once(plan: ShotList) -> None:
    """One text call. The deterministic repair already ran; this only logs."""
    key = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()
    if not key:
        return
    lines = []
    for shot in plan.shots:
        lines.append(
            f"{shot.id} | narration: {shot.narration} | "
            f"subject: {shot.subject} | setting: {shot.setting} | "
            f"scene: {shot.scene_type or shot.image_role}"
        )
    try:
        from core.google_guardrail import (
            guarded_generate_content,
            make_guarded_gemini_client,
        )

        client = make_guarded_gemini_client(key)
        response = guarded_generate_content(
            client,
            model="gemini-2.5-flash",
            contents=(
                "You check a documentary shot list. For each line, the picture "
                "must fit the narration without asking a generator to draw a "
                "named artefact, its parts, or a specific object. "
                "Reply with OK, or list the shot ids that do not fit. No prose.\n"
                + "\n".join(lines)
            ),
            kind="text",
            source="shot_planner.fit",
        )
        text = str(getattr(response, "text", "") or "").strip()
        _LOG.info("shot plan llm check | %s", text[:500] or "empty")
    except Exception as exc:  # noqa: BLE001
        _LOG.info("shot plan llm check skipped (%s)", type(exc).__name__)
