# -*- coding: utf-8 -*-
"""Run one beat up the fallback ladder and return a still-gate result."""
from __future__ import annotations

import contextvars
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Callable

from modules.reel_artefact.events import acquire_event_photo
from modules.reel_artefact.flux_policy import acceptable_flux_concept, safe_flux_prompt
from modules.reel_artefact.ladder import LadderResult, run_ladder
from modules.reel_artefact.logbook import note_accepted
from modules.reel_artefact.reuse import remember_approved
from modules.reel_artefact.role import beat_kind, is_event
from modules.reel_artefact.route import acquire_photo
from modules.reel_qa.models import StillGateResult
from modules.reel_qa.relevance import (
    evaluate_chunk_relevance,
    evaluate_with_technical_retry,
)
from modules.shot_planner.entities import Entity, entity_for_line, load_entities

logger = logging.getLogger(__name__)

# The fair scheduler sets this before a wave. "full" is the single-beat climb
# the tests use. "first" and "retry" judge one still and stop.
_SHARE: contextvars.ContextVar[str] = contextvars.ContextVar(
    "reel_ladder_share", default="full",
)
_CURSOR: dict[int, int] = {}
_CURSOR_LOCK = threading.Lock()

_ARCHIVAL = {
    "commons", "met", "smithsonian", "openverse", "europeana", "wikimedia",
}
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
    shot_subject: str = "",
    shot_era: str = "",
    image_role: str = "",
    scene_type: str = "",
    topic: str = "",
) -> StillGateResult:
    """One beat. Flag-off callers keep using generate_and_gate instead."""
    entity = _entity(channel, entity_id, narration, episode_subject)
    role = (image_role or "").strip().lower()
    # The planner's image_role wins. beat_kind() was turning planned object
    # photographs into Flux scenes, so Nano Banana never ran.
    if is_cta or role == "flux":
        kind = "scene"
    elif role == "photo":
        kind = "object"
    else:
        kind = beat_kind(
            entity,
            narration=narration,
            shot_type=shot_type,
            camera=camera,
            setting=setting,
        )
    geography = str(getattr(entity, "geography", "") or "") if entity else ""
    coastal = bool(getattr(entity, "coastal", False)) if entity else True
    planned_scene = (scene_type or "").strip().lower()
    judge_mode = "shot" if kind == "scene" else "object"
    folder = Path(output_directory)
    folder.mkdir(parents=True, exist_ok=True)
    safe = bool(entity and entity.text_only_safe) and kind == "object"
    shape = canonical_shape or (entity.canonical_shape if entity else "")
    # A line that names the object stays on the photograph ladder, even when
    # it also mentions the wreck the object came from.
    event = (
        (not is_cta)
        and kind != "object"
        and is_event(narration, camera=camera, setting=setting)
    )
    kwargs = dict(generate_kwargs or {})
    kwargs.pop("visual_role", None)
    producers = {
        "real": _real(entity, channel, beat_index),
        "event": _event(entity, channel),
        "nb-ref": _nano(entity, channel, beat_index, text_only=False),
        "nb-text": _nano(entity, channel, beat_index, text_only=True),
        "flux": _flux(
            flux_generate,
            kwargs,
            entity,
            scene_type=planned_scene,
            geography=geography,
            coastal=coastal,
        ),
        "reuse": _reuse(kind, entity.id if entity else ""),
    }

    def _judge(path: Path, ctx) -> dict:
        reference = ""
        found_shape = shape
        provider = ""
        meta: dict = {}
        try:
            from modules.reel_artefact.source import source_meta

            meta = source_meta(path)
            reference = str(meta.get("reference_image") or "")
            found_shape = str(meta.get("canonical_shape") or shape)
            provider = str(meta.get("provider") or "")
        except Exception:  # noqa: BLE001
            reference = ""
        nonlocal judge_mode
        still_kind = str(meta.get("still_kind") or "")
        fluxish = _flux_still(ctx.prompt, provider)
        # An event photograph is the divers or the boat, never the artefact.
        if still_kind == "event" or (ctx.event and provider in _ARCHIVAL and still_kind != "object"):
            judge_mode = "event"
        elif fluxish or (kind == "scene" and still_kind != "object" and provider not in _ARCHIVAL):
            judge_mode = "shot"
        else:
            judge_mode = "object"
        planned = shot_subject or _planned_subject(ctx.prompt)
        logger.info(
            "LADDER beat | beat=%s | scene=%s | role=%s | judge=%s",
            int(beat_index) + 1,
            planned_scene or kind,
            role or kind,
            judge_mode,
        )
        if judge_mode == "shot":
            judge_prompt = ctx.prompt
            against = "shot"
            judged_shape = ""
            judged_ref = ""
        elif judge_mode == "event":
            judge_prompt = ctx.prompt
            against = "event"
            judged_shape = ""
            judged_ref = ""
        else:
            judge_prompt = _judge_prompt(ctx.prompt, episode_subject)
            against = "narration"
            judged_shape = found_shape
            judged_ref = reference
        verdict = evaluate_with_technical_retry(
            evaluate_chunk_relevance,
            path,
            ctx.narration,
            judge_prompt,
            banned_subjects,
            reference_image=judged_ref,
            canonical_shape=judged_shape,
            score_against=against,
            shot_subject=planned,
            shot_setting=setting or geography,
            shot_era=shot_era,
            topic=topic or episode_subject,
            scene_type=planned_scene or kind,
            geography=geography,
        )
        verdict["judge_mode"] = judge_mode
        verdict["scene_type"] = planned_scene or kind
        verdict["image_role"] = role or kind
        return verdict

    share = _SHARE.get()
    start = cursor_for(beat_index) if share == "retry" else 0
    once = share in {"first", "retry"}
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
        event=event,
        name=entity.name if entity else "",
        period=str(getattr(entity, "event_period", "") or "") if entity else "",
        negatives=_flux_negatives(entity),
        llm=_gemini_reprompt if _llm_allowed() else None,
        once=once,
        start_rung=start,
    )
    if once:
        save_cursor(beat_index, result.next_rung)
    if result.passed and not result.resumed:
        note_accepted(result.rung or "flux")
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


def set_share(mode: str) -> None:
    """``full``, ``first``, or ``retry``. Workers copy this at submit time."""
    _SHARE.set(mode if mode in {"full", "first", "retry"} else "full")


def reset_cursors() -> None:
    with _CURSOR_LOCK:
        _CURSOR.clear()


def cursor_for(beat_index: int) -> int:
    with _CURSOR_LOCK:
        return int(_CURSOR.get(int(beat_index), 0))


def save_cursor(beat_index: int, nxt: int) -> None:
    with _CURSOR_LOCK:
        _CURSOR[int(beat_index)] = max(0, int(nxt))


def _flux_still(prompt: str, provider: str) -> bool:
    """A generated scene is scored against the plan, not the spoken object."""
    if provider in {
        "commons", "met", "smithsonian", "openverse", "europeana", "wikimedia", "reuse",
    }:
        return False
    return "do not depict a named artefact" in (prompt or "").lower()


def _planned_subject(prompt: str) -> str:
    match = re.search(r"EXPECTED SUBJECT:\s*([^.]+)", prompt or "", flags=re.IGNORECASE)
    if not match:
        return ""
    return " ".join(match.group(1).split())


def _entity(
    channel: str,
    entity_id: str,
    narration: str,
    episode_subject: str,
) -> Entity | None:
    """The topic entity, unless this spoken line names a different one."""
    try:
        rows = load_entities(channel or "ancient_knowledge")
    except Exception:  # noqa: BLE001
        return None
    return entity_for_line(
        narration or "",
        rows,
        topic=episode_subject or "",
        proposed_id=entity_id or "",
    )


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
            n_beats=ctx.n_beats,
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
            n_beats=ctx.n_beats,
        )
    return _run


def _event(entity: Entity | None, channel: str):
    def _run(ctx) -> Path | None:
        if entity is None or not ctx.event:
            return None
        return acquire_event_photo(
            entity,
            ctx.dest,
            channel=channel,
            beat_index=ctx.beat_index,
            n_beats=ctx.n_beats,
        )
    return _run


def _flux_negatives(entity: Entity | None) -> tuple[str, ...]:
    if entity is None:
        return ()
    found: list[str] = []
    for key in ("edit_negatives", "event_negatives"):
        for item in getattr(entity, key, ()) or ():
            text = str(item).strip()
            if text and text not in found:
                found.append(text)
    return tuple(found)


def _flux(
    generate: Callable[..., Any],
    kwargs: dict,
    entity: Entity | None,
    *,
    scene_type: str = "",
    geography: str = "",
    coastal: bool = True,
):
    def _run(ctx):
        name = entity.name if entity else ""
        period = str(getattr(entity, "event_period", "") or "") if entity else ""
        negatives = _flux_negatives(entity)
        # The planned scene leads. A later rung or a re-plan steps to the next
        # allowed scene, still in this entity's real geography.
        variant = int(ctx.replan or 0)
        if scene_type and not ctx.replan:
            variant = int(ctx.attempt or 0)
        # Flux never receives a prompt whose subject is the named object.
        # An object beat that reached this rung has already missed real and
        # Nano Banana, so the still is a Flux-safe context scene.
        text = safe_flux_prompt(
            ctx.narration,
            name=name,
            negatives=negatives,
            period=period,
            attempt=max(1, int(ctx.replan or ctx.attempt or 1)),
            event=bool(ctx.event),
            kind="object" if ctx.kind == "object" and not ctx.event else "scene",
            rejection=ctx.rejection or "",
            scene=scene_type,
            geography=geography,
            coastal=coastal if entity is not None else True,
            variant=variant,
        )
        aliases = tuple(getattr(entity, "aliases", ()) or ())
        if ctx.concept and acceptable_flux_concept(ctx.concept, name=name, aliases=aliases):
            # The safe scene stays the subject. A clean variation may follow it.
            text = f"{text} Variation: {ctx.concept}"
        elif ctx.concept:
            logger.info(
                "FLUX replan rejected | beat=%s | concept names a forbidden object",
                int(ctx.beat_index) + 1,
            )
        ctx.prompt = text
        logger.info(
            "FLUX prompt | beat=%s | attempt=%s | %s",
            int(ctx.beat_index) + 1,
            int(ctx.replan or 0) or (int(ctx.attempt) + 1),
            text,
        )
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


def _llm_allowed() -> bool:
    disabled = (os.getenv("REEL_LADDER_LLM") or "").strip().lower() in {
        "0", "off", "false", "no",
    }
    if disabled:
        return False
    return bool((os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip())


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
    publishable = bool(result.passed) and result.path is not None
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
