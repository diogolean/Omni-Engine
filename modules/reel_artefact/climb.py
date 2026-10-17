# -*- coding: utf-8 -*-
"""Run one beat up the fallback ladder and return a still-gate result."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Callable

from modules.reel_artefact.ladder import (
    LadderResult,
    reprompt_from_defect,
    run_ladder,
)
from modules.reel_artefact.reuse import remember_approved
from modules.reel_artefact.role import beat_kind
from modules.reel_artefact.route import acquire_photo, generic_flux_prompt
from modules.reel_qa.models import StillGateResult
from modules.reel_qa.relevance import (
    evaluate_chunk_relevance,
    evaluate_with_technical_retry,
)
from modules.shot_planner.entities import Entity, entities_in_text, load_entities

logger = logging.getLogger(__name__)

_FLUX_KEYS = {
    "negative_prompt",
    "aspect_ratio",
    "width",
    "height",
    "draft",
    "steps",
}


def climb_act(
    *,
    beat_index: int,
    narration: str,
    prompt: str,
    output_directory: Path | str,
    stem: str,
    channel: str,
    flux_generate: Callable[..., Any],
    entity_id: str = "",
    checkpoint: Any = None,
    generate_kwargs: dict | None = None,
    episode_subject: str = "",
    canonical_shape: str = "",
    banned_subjects: list[str] | None = None,
    is_cta: bool = False,
    n_beats: int = 0,
    shot_type: str = "",
    camera: str = "",
    setting: str = "",
) -> StillGateResult:
    """One beat. Flag-off callers keep using generate_and_gate instead."""
    entity = _entity(channel, entity_id, narration, episode_subject)
    kind = "scene" if is_cta else beat_kind(
        entity,
        narration=narration,
        shot_type=shot_type,
        camera=camera,
        setting=setting,
    )
    folder = Path(output_directory)
    folder.mkdir(parents=True, exist_ok=True)
    safe = bool(entity and entity.text_only_safe) and kind == "object"
    shape = canonical_shape or (entity.canonical_shape if entity else "")
    kwargs = dict(generate_kwargs or {})
    kwargs.pop("visual_role", None)
    producers = {
        "real": _real(entity, channel, beat_index),
        "nb-ref": _nano(entity, channel, beat_index, text_only=False),
        "nb-text": _nano(entity, channel, beat_index, text_only=True),
        "flux": _flux(flux_generate, kwargs),
        "reuse": _reuse(kind, entity.id if entity else ""),
    }

    def _judge(path: Path, ctx) -> dict:
        reference = ""
        found_shape = shape
        try:
            from modules.reel_artefact.source import source_meta

            meta = source_meta(path)
            reference = str(meta.get("reference_image") or "")
            found_shape = str(meta.get("canonical_shape") or shape)
        except Exception:  # noqa: BLE001
            reference = ""
        return evaluate_with_technical_retry(
            evaluate_chunk_relevance,
            path,
            ctx.narration,
            _judge_prompt(ctx.prompt, episode_subject),
            banned_subjects,
            reference_image=reference,
            canonical_shape=found_shape,
        )

    result = run_ladder(
        beat_index=beat_index,
        narration=narration,
        prompt=prompt,
        dest_dir=folder,
        kind=kind,
        text_only_safe=safe,
        n_beats=n_beats,
        producers=producers,
        judge=_judge,
        checkpoint=checkpoint,
        entity_id=entity.id if entity else "",
    )
    if result.passed and result.path is not None and not result.resumed:
        remember_approved(result.path, kind=kind)
        if checkpoint is not None:
            try:
                checkpoint.save_beat(
                    beat_index,
                    image=result.path,
                    score=result.score,
                    reason=result.reason,
                    rung=result.rung,
                    verdict=result.verdict,
                    first_pass=result.first_pass,
                    kind=kind,
                )
            except Exception as exc:  # noqa: BLE001
                logger.info("LADDER checkpoint skipped (%s)", type(exc).__name__)
    return _gate(result, stem=stem)


def _entity(
    channel: str,
    entity_id: str,
    narration: str,
    episode_subject: str,
) -> Entity | None:
    try:
        rows = load_entities(channel or "ancient_knowledge")
    except Exception:  # noqa: BLE001
        return None
    if entity_id:
        for item in rows:
            if item.id == entity_id:
                return item
    hits = entities_in_text(narration or "", rows) or entities_in_text(episode_subject or "", rows)
    return hits[0] if hits else None


def _real(entity: Entity | None, channel: str, beat_index: int):
    def _run(ctx) -> Path | None:
        if entity is None or ctx.kind == "scene":
            return None
        dest = ctx.dest
        return acquire_photo(
            entity,
            dest,
            channel=channel,
            mode="chain",
            beat_index=beat_index,
            allow_nano=False,
        )
    return _run


def _nano(entity: Entity | None, channel: str, beat_index: int, *, text_only: bool):
    def _run(ctx) -> Path | None:
        if entity is None or ctx.kind == "scene":
            return None
        return acquire_photo(
            entity,
            ctx.dest,
            channel=channel,
            mode="chain",
            beat_index=beat_index,
            allow_nano=True,
            text_only=text_only,
            skip_real=not text_only,
        )
    return _run


def _flux(generate: Callable[..., Any], kwargs: dict):
    def _run(ctx):
        if ctx.rejection:
            text = reprompt_from_defect(
                ctx.narration,
                ctx.prompt,
                ctx.rejection,
                llm=_gemini_reprompt,
            )
        else:
            text = generic_flux_prompt(ctx.prompt or ctx.narration)
        call = {key: value for key, value in kwargs.items() if key in _FLUX_KEYS}
        call["output_stem"] = ctx.dest.stem
        call["output_directory"] = ctx.dest.parent
        call["visual_role"] = "documentary"
        call["avatar_mode"] = "OFF"
        made = generate(text, **call)
        return Path(made) if made else None
    return _run


def _reuse(kind: str, entity_id: str):
    def _run(ctx):
        from modules.reel_artefact.reuse import approved_still, reuse_into

        origin = approved_still(kind)
        if origin is None:
            return None
        return reuse_into(
            ctx.dest,
            ctx.beat_index,
            entity_id=entity_id,
            source=origin,
            kind=kind,
            n_beats=ctx.n_beats,
        )
    return _run


def _gemini_reprompt(narration: str, prompt: str, rejection: str) -> str | None:
    if (os.getenv("REEL_LADDER_LLM") or "").strip().lower() in {"0", "off", "false", "no"}:
        return None
    key = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()
    if not key:
        return None
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
                "Rewrite this as one photoreal documentary image prompt. "
                "35mm film grain, natural colour, real location. "
                "No cyberpunk and no illustration. "
                f"Narration: {narration}\nRejected because: {rejection}\n"
                f"Previous prompt: {prompt}\nReturn only the prompt."
            ),
            kind="text",
            source="reel_artefact.ladder",
        )
        text = str(getattr(response, "text", "") or "").strip()
        return text or None
    except Exception:  # noqa: BLE001
        return None


def _judge_prompt(prompt: str, episode_subject: str) -> str:
    subject = " ".join((episode_subject or "").split())
    body = prompt or ""
    if not subject or subject.lower() in body[:500].lower():
        return body
    return f"EXPECTED SUBJECT: {subject}. {body}".strip()


def _gate(result: LadderResult, *, stem: str) -> StillGateResult:
    verdict = dict(result.verdict or {})
    verdict.update({
        "ladder": True,
        "resumed": result.resumed,
        "fail_reel": result.fail_reel,
        "kept": result.kept,
        "rung": result.rung,
        "ladder_history": ",".join(
            f"{row.rung}:{'pass' if row.status == 'pass' else row.status}"
            for row in result.history
        ),
        "spend": dict(result.spend),
        "beat_kind": result.kind,
        "is_relevant": bool(result.passed),
        "relevance_score": result.score if result.score is not None else verdict.get(
            "relevance_score", 0.0
        ),
        "qa_status": verdict.get("qa_status") or ("scored" if result.passed else "scored"),
        "rejection_reason": "" if result.passed else (result.reason or "ladder_exhausted"),
    })
    if result.resumed:
        verdict["is_relevant"] = True
        verdict["qa_status"] = verdict.get("qa_status") or "scored"
    publishable = bool(result.passed or result.kept) and result.path is not None
    flux_fails = sum(
        1 for row in result.history if row.rung == "flux" and row.status == "fail"
    )
    attempts = [
        {
            "attempt": index + 1,
            "qa_status": row.status,
            "rejection_reason": row.reason,
            "provider": row.rung,
            "relevance_score": result.score if row.status == "pass" else 0.0,
            "is_relevant": row.status == "pass",
        }
        for index, row in enumerate(result.history)
    ] or [verdict]
    blocked = ""
    if result.fail_reel:
        blocked = "ladder_exhausted"
    elif result.kept:
        blocked = ""
    logger.info(
        "LADDER gate | stem=%s publishable=%s rung=%s",
        stem,
        publishable,
        result.rung,
    )
    return StillGateResult(
        path=result.path if result.path is not None and result.path.is_file() else None,
        extra_generations=flux_fails,
        publishable=publishable,
        blocked_reason=blocked,
        attempts=attempts,
        verdict=verdict,
        first_pass=result.first_pass,
    )
