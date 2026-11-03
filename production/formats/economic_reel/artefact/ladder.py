# -*- coding: utf-8 -*-
"""Per-beat fallback ladder. Stop at the first still the strict judge accepts."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from production.formats.economic_reel.artefact.budget import (
    beat_budget_usd,
    nano_cap,
    nano_used,
    reel_budget_usd,
    reel_spent,
    refund,
    release_flux_slot,
    take_flux_slot,
    take_replan_slot,
    try_spend,
)
from production.formats.economic_reel.artefact.pricing import FLUX_USD, NANOBANANA_MODELS, price_for_model
from production.qa.relevance import verdict_passed

logger = logging.getLogger(__name__)

# Reuse an approved object still before a landscape. A missed photograph
# should stay a photograph, with a different crop, while a real photo or a
# Nano Banana edit is still available.
_DEFAULT = ("real", "nb-ref", "nb-text", "reuse", "flux")
_NANO = 0.0336
# One Flux image, then a different crop of a still that already passed.
# A second Flux rung in this list was a second image inside pass 1.
_SCENE = ("flux", "reuse")

Producer = Callable[["RungContext"], Path | str | None]
Judge = Callable[[Path, "RungContext"], dict]


def _nano_usd() -> float:
    try:
        return float(price_for_model(NANOBANANA_MODELS[0]))
    except Exception:  # noqa: BLE001
        return _NANO


def rung_cost(name: str) -> float:
    if name in {"nb-ref", "nb-text"}:
        return _nano_usd()
    if name == "flux":
        return FLUX_USD
    return 0.0


def rung_names() -> list[str]:
    """Object-beat order. Flux Dev stays off: the A/B lost to Schnell.

    Nano Banana rungs drop out while ``REEL_NANOBANANA_MAX_PER_REEL`` is 0
    (the near-zero default), so a missed photograph goes straight to reuse
    and then to the per-scene Flux fallback.
    """
    raw = (os.getenv("REEL_LADDER") or "").strip()
    if raw:
        names = [part.strip() for part in raw.split(",") if part.strip()]
    else:
        names = list(_DEFAULT)
        if nano_cap() <= 0:
            names = [name for name in names if name not in {"nb-ref", "nb-text"}]
    if "flux-dev" in names:
        logger.info("LADDER | flux-dev dropped | Schnell won the A/B")
        names = [name for name in names if name != "flux-dev"]
    return names or list(_DEFAULT)


def rungs_for(kind: str, *, event: bool = False) -> list[str]:
    """Scene beats never climb the artefact-photo rungs.

    An event beat looks for an archival photograph before Flux.
    """
    if event:
        return ["event", "flux", "reuse"]
    if kind == "scene":
        return list(_SCENE)
    return rung_names()


@dataclass
class RungContext:
    beat_index: int
    narration: str
    prompt: str
    dest: Path
    kind: str = "object"
    rejection: str = ""
    attempt: int = 0
    n_beats: int = 0
    concept: str = ""
    replan: int = 0
    event: bool = False


@dataclass
class RungAttempt:
    rung: str
    status: str
    reason: str = ""
    cost_usd: float = 0.0
    path: str = ""


@dataclass
class LadderResult:
    path: Path | None = None
    passed: bool = False
    resumed: bool = False
    rung: str = ""
    history: list[RungAttempt] = field(default_factory=list)
    verdict: dict = field(default_factory=dict)
    fail_reel: bool = False
    kept: bool = False
    first_pass: bool | None = None
    score: float | None = None
    reason: str = ""
    spend: dict[str, int] = field(default_factory=dict)
    kind: str = "object"
    beat_spent: float = 0.0
    replans: int = 0
    # Next rung index a later share should start at. A fair reel uses this
    # so the retry wave does not repeat the attempt this beat already had.
    next_rung: int = 0


def history_text(history: list[RungAttempt]) -> str:
    parts = []
    for row in history:
        if row.status == "pass":
            parts.append(f"{row.rung}:pass")
        elif row.status == "skip":
            parts.append(f"{row.rung}:skip-{row.reason or 'skip'}")
        else:
            parts.append(f"{row.rung}:fail")
    return ",".join(parts)


def reprompt_from_defect(
    narration: str,
    base_prompt: str,
    rejection: str,
    llm: Callable[..., str | None] | None = None,
) -> str:
    """Documentary re-prompt. The judge threshold is not changed."""
    reason = " ".join((rejection or "").split())
    spoken = " ".join((narration or "").split())
    bits = [
        "Photoreal documentary photograph from a real camera, 35mm film grain,",
        "natural colour, real location. Not a render, not CGI, not an illustration.",
        "Simple composition. A museum hall or a landscape establishing shot",
        "that still fits the narration.",
        f"Narration: {spoken}.",
    ]
    if reason:
        bits.append(f"The previous still was rejected: {reason}.")
    if "non_photoreal" in reason:
        bits.append("This must be a real photograph, not a render.")
    if "anachronism" in reason:
        bits.append("No modern objects.")
    fallback = " ".join(bits)
    disabled = (os.getenv("REEL_LADDER_LLM") or "").strip().lower() in {
        "0", "off", "false", "no",
    }
    if disabled or llm is None:
        return fallback
    try:
        text = llm(spoken, base_prompt or "", reason)
    except Exception:  # noqa: BLE001
        return fallback
    if isinstance(text, str) and len(text.strip()) > 40:
        return text.strip()
    return fallback


def _bill_key(rung: str, path: Path | None) -> str:
    if rung in {"nb-ref", "nb-text"}:
        model = ""
        if path is not None:
            from production.formats.economic_reel.artefact.source import source_meta

            model = str(source_meta(path).get("model_id") or "")
        if model and price_for_model(model) > 0.034:
            return "image_nanobanana"
        return "image_nanobanana_lite"
    if rung == "flux":
        return "image_flux_schnell"
    if rung == "real" or rung == "event":
        return "image_commons"
    if rung == "reuse":
        return "image_reuse"
    return ""


def _skip(history: list[RungAttempt], rung: str, reason: str, beat: int) -> None:
    history.append(RungAttempt(rung=rung, status="skip", reason=reason))
    logger.info(
        "LADDER | beat=%s | rung=%s | status=skip-%s | spent_reel=%.4f",
        beat,
        rung,
        reason,
        reel_spent(),
    )


def _budget_can_pay(beat_spent: float, cost: float | None = None) -> bool:
    needed = FLUX_USD if cost is None else cost
    if beat_spent + needed > beat_budget_usd() + 1e-9:
        return False
    if reel_spent() + needed > reel_budget_usd() + 1e-9:
        return False
    return True


def run_ladder(
    *,
    beat_index: int,
    narration: str,
    prompt: str,
    dest_dir: Path | str,
    kind: str = "object",
    text_only_safe: bool = False,
    n_beats: int = 0,
    producers: dict[str, Producer] | None = None,
    judge: Judge | None = None,
    checkpoint: Any = None,
    entity_id: str = "",
    event: bool = False,
    name: str = "",
    period: str = "",
    negatives: tuple[str, ...] | list[str] = (),
    llm: Callable[..., str | None] | None = None,
    once: bool = False,
    start_rung: int = 0,
) -> LadderResult:
    """Climb until one still passes. Do not lower the 0.70 judge.

    A failed rung is never published. When the ladder is exhausted and the
    beat budget can still pay for another Flux still, the concept is
    re-planned up to ``REEL_REPLAN_MAX`` times (default 2), and only while
    ``REEL_REPLAN_MAX_PER_REEL`` still has room.

    ``once`` stops after the first judged still. The fair scheduler uses
    that so every beat gets an attempt before any beat is retried.
    ``start_rung`` skips rungs this beat already judged.
    """
    del entity_id
    from production.formats.economic_reel.artefact.replan import next_concept, replan_limit

    order = rungs_for(kind, event=bool(event))
    begin = max(0, int(start_rung or 0))
    if once and begin >= len(order):
        return _one_replan(
            beat_index=beat_index,
            narration=narration,
            prompt=prompt,
            dest_dir=dest_dir,
            kind=kind,
            text_only_safe=text_only_safe,
            n_beats=n_beats,
            producers=producers,
            judge=judge,
            event=event,
            name=name,
            period=period,
            negatives=negatives,
            llm=llm,
            start_rung=begin,
        )

    result = _climb(
        beat_index=beat_index,
        narration=narration,
        prompt=prompt,
        dest_dir=dest_dir,
        kind=kind,
        text_only_safe=text_only_safe,
        n_beats=n_beats,
        producers=producers,
        judge=judge,
        checkpoint=checkpoint,
        event=event,
        once=once,
        start_rung=begin,
    )
    if result.passed or result.resumed or once:
        return result
    history = list(result.history)
    spent = result.beat_spent
    spend = dict(result.spend)
    reason = result.reason
    done = 0
    for attempt in range(1, replan_limit() + 1):
        if not _budget_can_pay(spent):
            logger.info(
                "LADDER | beat=%s | replan=skip-budget | spent_beat=%.4f",
                int(beat_index) + 1,
                spent,
            )
            break
        if not take_replan_slot():
            logger.info(
                "LADDER | beat=%s | replan=skip-cap",
                int(beat_index) + 1,
            )
            break
        concept = next_concept(
            narration,
            kind=kind,
            event=event,
            name=name,
            period=period,
            rejection=reason,
            attempt=attempt,
            negatives=negatives,
            llm=llm,
        )
        logger.info(
            "LADDER | beat=%s | replan=%s | concept=%.80s",
            int(beat_index) + 1,
            attempt,
            concept,
        )
        nxt = _climb(
            beat_index=beat_index,
            narration=narration,
            prompt=concept,
            dest_dir=dest_dir,
            kind=kind,
            text_only_safe=text_only_safe,
            n_beats=n_beats,
            producers=producers,
            judge=judge,
            checkpoint=None,
            event=event,
            concept=concept,
            replan=attempt,
            beat_spent_in=spent,
        )
        history.extend(nxt.history)
        spent = nxt.beat_spent
        for key_name, count in nxt.spend.items():
            spend[key_name] = spend.get(key_name, 0) + int(count)
        reason = nxt.reason or reason
        done = attempt
        if nxt.passed:
            nxt.history = history
            nxt.first_pass = False
            nxt.replans = attempt
            nxt.kept = False
            nxt.spend = spend
            nxt.beat_spent = spent
            return nxt
    _finish_log(beat_index, history, True, spent, kept=False)
    return LadderResult(
        path=None,
        passed=False,
        rung="",
        history=history,
        verdict=result.verdict,
        fail_reel=True,
        kept=False,
        first_pass=False,
        score=result.score,
        reason=reason or "ladder_exhausted",
        spend=spend,
        kind=kind or "object",
        beat_spent=spent,
        replans=done,
    )


def _one_replan(
    *,
    beat_index: int,
    narration: str,
    prompt: str,
    dest_dir: Path | str,
    kind: str,
    text_only_safe: bool,
    n_beats: int,
    producers: dict[str, Producer] | None,
    judge: Judge | None,
    event: bool,
    name: str,
    period: str,
    negatives: tuple[str, ...] | list[str],
    llm: Callable[..., str | None] | None,
    start_rung: int,
) -> LadderResult:
    """One re-planned Flux still, after this beat has used every rung."""
    from production.formats.economic_reel.artefact.replan import next_concept

    if not take_replan_slot():
        from production.formats.economic_reel.artefact.budget import scene_regen_allowed

        if not scene_regen_allowed(kind):
            logger.info("LADDER | beat=%s | replan=skip-cap", int(beat_index) + 1)
            return LadderResult(
                passed=False,
                fail_reel=True,
                kept=False,
                reason="replan_cap",
                kind=kind or "object",
                next_rung=start_rung,
            )
        logger.info(
            "LADDER | beat=%s | replan=budget | kind=%s",
            int(beat_index) + 1,
            kind,
        )
    concept = next_concept(
        narration,
        kind=kind,
        event=event,
        name=name,
        period=period,
        rejection="",
        attempt=max(1, start_rung),
        negatives=negatives,
        llm=llm,
    )
    logger.info(
        "LADDER | beat=%s | replan=share | concept=%.80s",
        int(beat_index) + 1,
        concept,
    )
    made = _climb(
        beat_index=beat_index,
        narration=narration,
        prompt=concept,
        dest_dir=dest_dir,
        kind=kind,
        text_only_safe=text_only_safe,
        n_beats=n_beats,
        producers=producers,
        judge=judge,
        checkpoint=None,
        event=event,
        concept=concept,
        replan=1,
        once=True,
        only_rung="flux",
    )
    made.next_rung = start_rung + 1
    made.replans = 1 if made.spend else made.replans
    made.first_pass = False
    return made


def _climb(
    *,
    beat_index: int,
    narration: str,
    prompt: str,
    dest_dir: Path | str,
    kind: str = "object",
    text_only_safe: bool = False,
    n_beats: int = 0,
    producers: dict[str, Producer] | None = None,
    judge: Judge | None = None,
    checkpoint: Any = None,
    event: bool = False,
    concept: str = "",
    replan: int = 0,
    beat_spent_in: float = 0.0,
    once: bool = False,
    start_rung: int = 0,
    only_rung: str = "",
) -> LadderResult:
    folder = Path(dest_dir)
    ctx = RungContext(
        beat_index=int(beat_index),
        narration=narration or "",
        prompt=prompt or "",
        dest=folder / "ladder.jpg",
        kind=kind or "object",
        n_beats=max(0, int(n_beats or 0)),
        concept=concept or "",
        replan=int(replan or 0),
        event=bool(event),
    )
    calls = producers or {}
    if checkpoint is not None:
        saved = checkpoint.approved_beat(int(beat_index))
        if saved and Path(str(saved.get("path") or "")).is_file():
            path = Path(str(saved["path"]))
            if _reserve(path, ctx.n_beats) and _diverse(path, int(beat_index)):
                from production.formats.economic_reel.artefact.reuse import (
                    note_beat_key,
                    remember_approved,
                    reuse_key_for,
                )

                remember_approved(path, kind=str(saved.get("kind") or kind or "object"))
                note_beat_key(int(beat_index), reuse_key_for(path))
                logger.info(
                    "LADDER | beat=%s | history=resume:pass | fail_reel=False | spent_beat=0",
                    int(beat_index) + 1,
                )
                return LadderResult(
                    path=path,
                    passed=True,
                    resumed=True,
                    rung=str(saved.get("rung") or "resume"),
                    verdict={"resumed": True, "ladder": True, **(saved.get("verdict") or {})},
                    first_pass=saved.get("first_pass"),
                    score=saved.get("score"),
                    reason=str(saved.get("reason") or ""),
                    kind=str(saved.get("kind") or kind),
                )
            _release(path)

    history: list[RungAttempt] = []
    spend: dict[str, int] = {}
    beat_spent = max(0.0, float(beat_spent_in or 0.0))
    saw_scored_fail = bool(replan)
    last_verdict: dict = {}
    order = rungs_for(ctx.kind, event=bool(event))
    budget = beat_budget_usd()

    begin = max(0, int(start_rung or 0))
    flux_used = False
    for attempt, rung in enumerate(order):
        if attempt < begin:
            continue
        if only_rung and rung != only_rung:
            continue
        # Pass 1 and each later share take one Flux image. The second Flux
        # rung is the next share's variant, not a second image in this climb.
        if rung == "flux" and once and flux_used:
            return _judged_stop(
                history=history,
                verdict=last_verdict,
                spend=spend,
                kind=ctx.kind,
                beat_spent=beat_spent,
                replan=replan,
                reason="one-flux",
                next_rung=attempt,
            )
        if rung == "flux":
            flux_used = True
        ctx.attempt = attempt
        stem = f"ladder_{int(beat_index):02d}_{rung}_{attempt:02d}"
        if replan:
            stem = f"{stem}_p{int(replan):02d}"
        ctx.dest = folder / f"{stem}.jpg"
        if rung == "nb-text" and not text_only_safe:
            _skip(history, rung, "unsafe", int(beat_index) + 1)
            continue
        if rung in {"nb-ref", "nb-text"} and nano_used() >= nano_cap():
            _skip(history, rung, "cap", int(beat_index) + 1)
            continue
        if rung == "reuse":
            reason = _reuse_block(ctx)
            if reason and reason != "none":
                _skip(history, rung, reason, int(beat_index) + 1)
                continue
        producer = calls.get(rung)
        if producer is None:
            _skip(history, rung, "no-producer", int(beat_index) + 1)
            continue
        cost = rung_cost(rung)
        charged = False
        flux_slot = False
        if rung == "flux":
            if not take_flux_slot():
                _skip(history, rung, "flux-cap", int(beat_index) + 1)
                if once:
                    return _judged_stop(
                        history=history,
                        verdict=last_verdict,
                        spend=spend,
                        kind=ctx.kind,
                        beat_spent=beat_spent,
                        replan=replan,
                        reason="flux-cap",
                        next_rung=attempt,
                    )
                continue
            flux_slot = True
        if cost > 0:
            if beat_spent + cost > budget + 1e-9:
                _skip(history, rung, "beat-budget", int(beat_index) + 1)
                continue
            if not try_spend(cost):
                _skip(history, rung, "reel-budget", int(beat_index) + 1)
                continue
            beat_spent += cost
            charged = True
        try:
            made = producer(ctx)
        except Exception as exc:  # noqa: BLE001
            if flux_slot:
                release_flux_slot()
            history.append(RungAttempt(
                rung=rung, status="fail", reason=type(exc).__name__, cost_usd=cost,
            ))
            logger.info(
                "LADDER | beat=%s | rung=%s | status=fail | cost=%.4f | %s",
                int(beat_index) + 1, rung, cost, type(exc).__name__,
            )
            if once and rung == "flux":
                return _judged_stop(
                    history=history,
                    verdict=last_verdict,
                    spend=spend,
                    kind=ctx.kind,
                    beat_spent=beat_spent,
                    replan=replan,
                    reason=type(exc).__name__,
                    next_rung=attempt + 1,
                )
            continue
        path = Path(made) if made else None
        if path is None or not path.is_file():
            if flux_slot:
                release_flux_slot()
                flux_slot = False
            if charged:
                refund(cost)
                beat_spent = max(0.0, beat_spent - cost)
                charged = False
            history.append(RungAttempt(rung=rung, status="fail", reason="missing"))
            # Pass 1 is one Flux image. A missing file does not unlock the
            # second Flux rung in the same climb.
            if once and rung == "flux":
                return _judged_stop(
                    history=history,
                    verdict=last_verdict,
                    spend=spend,
                    kind=ctx.kind,
                    beat_spent=beat_spent,
                    replan=replan,
                    reason="missing",
                    next_rung=attempt + 1,
                )
            continue
        shown = _shown_rung(rung, path)
        if charged and (shown == "reuse" or _already_paid(path)):
            refund(cost)
            beat_spent = max(0.0, beat_spent - cost)
            charged = False
        if flux_slot and shown == "reuse":
            release_flux_slot()
            flux_slot = False
        reserved = _reserve(path, ctx.n_beats)
        if not reserved:
            if charged:
                refund(cost)
                beat_spent = max(0.0, beat_spent - cost)
            history.append(RungAttempt(
                rung=shown, status="skip", reason="still", path=str(path),
            ))
            if once and rung == "flux":
                return _judged_stop(
                    history=history,
                    verdict=last_verdict,
                    spend=spend,
                    kind=ctx.kind,
                    beat_spent=beat_spent,
                    replan=replan,
                    reason="still",
                    next_rung=attempt + 1,
                )
            continue
        if charged:
            key = _bill_key(shown, path)
            if key:
                spend[key] = spend.get(key, 0) + 1
        elif shown in {"real", "reuse", "event"}:
            key = _bill_key(shown, path)
            if key:
                spend[key] = spend.get(key, 0) + 1
        if judge is None:
            verdict = {
                "is_relevant": True,
                "relevance_score": 1.0,
                "qa_status": "scored",
                "rejection_reason": "",
            }
        else:
            try:
                verdict = dict(judge(path, ctx) or {})
            except Exception as exc:  # noqa: BLE001
                verdict = {
                    "is_relevant": False,
                    "relevance_score": 0.0,
                    "qa_status": "unavailable",
                    "rejection_reason": f"qa_unavailable:{type(exc).__name__}",
                }
        last_verdict = verdict
        ctx.rejection = str(verdict.get("rejection_reason") or "")
        scored = verdict.get("qa_status") != "unavailable"
        if verdict_passed(verdict):
            if not _diverse(path, int(beat_index)):
                _release(path)
                saw_scored_fail = True
                history.append(RungAttempt(
                    rung=shown,
                    status="fail",
                    reason="near-duplicate",
                    path=str(path),
                ))
                logger.info(
                    "LADDER | beat=%s | rung=%s | status=fail | near-duplicate",
                    int(beat_index) + 1,
                    shown,
                )
                if once:
                    return _judged_stop(
                        history=history,
                        verdict=verdict,
                        spend=spend,
                        kind=ctx.kind,
                        beat_spent=beat_spent,
                        replan=replan,
                        reason="near-duplicate",
                        next_rung=attempt + 1,
                        score=_score(verdict),
                    )
                continue
            first = not saw_scored_fail
            passed_row = RungAttempt(
                rung=shown, status="pass", path=str(path), cost_usd=cost if charged else 0.0,
            )
            _finish_log(beat_index, history + [passed_row], False, beat_spent, kept=False)
            return LadderResult(
                path=path,
                passed=True,
                rung=shown,
                history=history + [passed_row],
                verdict=verdict,
                first_pass=first if scored else False,
                score=_score(verdict),
                reason="",
                spend=spend,
                kind=ctx.kind,
                beat_spent=beat_spent,
                replans=int(replan or 0),
                next_rung=attempt + 1,
            )
        if scored:
            saw_scored_fail = True
        _release(path)
        history.append(RungAttempt(
            rung=shown,
            status="fail",
            reason=ctx.rejection or "rejected",
            cost_usd=cost if charged else 0.0,
            path=str(path),
        ))
        logger.info(
            "LADDER | beat=%s | rung=%s | status=fail | cost=%.4f | %s",
            int(beat_index) + 1,
            shown,
            cost if charged else 0.0,
            ctx.rejection or "rejected",
        )
        if once:
            return _judged_stop(
                history=history,
                verdict=last_verdict,
                spend=spend,
                kind=ctx.kind,
                beat_spent=beat_spent,
                replan=replan,
                reason=str(last_verdict.get("rejection_reason") or "rejected"),
                next_rung=attempt + 1,
                score=_score(last_verdict),
            )

    _finish_log(beat_index, history, True, beat_spent, kept=False)
    return LadderResult(
        path=None,
        passed=False,
        rung="",
        history=history,
        verdict=last_verdict,
        fail_reel=True,
        kept=False,
        first_pass=False,
        score=_score(last_verdict),
        reason=str(last_verdict.get("rejection_reason") or "ladder_exhausted"),
        spend=spend,
        kind=ctx.kind,
        beat_spent=beat_spent,
        replans=int(replan or 0),
        next_rung=len(order),
    )


def _judged_stop(
    *,
    history: list[RungAttempt],
    verdict: dict,
    spend: dict[str, int],
    kind: str,
    beat_spent: float,
    replan: int,
    reason: str,
    next_rung: int,
    score: float | None = None,
) -> LadderResult:
    """One share is over. The still is not published."""
    return LadderResult(
        path=None,
        passed=False,
        rung="",
        history=history,
        verdict=verdict,
        fail_reel=True,
        kept=False,
        first_pass=False,
        score=score if score is not None else _score(verdict),
        reason=reason or "rejected",
        spend=spend,
        kind=kind,
        beat_spent=beat_spent,
        replans=int(replan or 0),
        next_rung=next_rung,
    )


def _reuse_block(ctx: RungContext) -> str:
    from production.formats.economic_reel.artefact.budget import reuse_room
    from production.formats.economic_reel.artefact.reuse import approved_still, reuse_key_for

    origin = approved_still(ctx.kind)
    if origin is None:
        return "none"
    allowed, reason = reuse_room(reuse_key_for(origin), ctx.n_beats)
    if not allowed:
        return reason or "cap"
    return ""


def _already_paid(path: Path) -> bool:
    from production.formats.economic_reel.artefact.source import source_meta

    provider = str(source_meta(path).get("provider") or "")
    return provider in {
        "reuse", "commons", "met", "smithsonian", "openverse", "europeana", "wikimedia",
    }


def _shown_rung(rung: str, path: Path) -> str:
    from production.formats.economic_reel.artefact.source import source_meta

    provider = str(source_meta(path).get("provider") or "")
    if provider == "reuse":
        return "reuse"
    return rung


def _content_key(path: Path) -> str:
    from production.formats.economic_reel.artefact.budget import file_hash_key
    from production.formats.economic_reel.artefact.source import source_meta

    meta = source_meta(path)
    saved = str(meta.get("image_hash") or meta.get("reuse_key") or "")
    if saved.startswith("sha256:"):
        return saved
    try:
        return file_hash_key(str(path))
    except OSError:
        try:
            return str(path.resolve())
        except OSError:
            return str(path)


def _reserve(path: Path, n_beats: int) -> bool:
    from production.formats.economic_reel.artefact.budget import claim_still

    try:
        target = str(path.resolve())
    except OSError:
        target = str(path)
    ok, _reason = claim_still(target, _content_key(path), n_beats)
    return ok


def _release(path: Path) -> None:
    from production.formats.economic_reel.artefact.budget import release_noted_reuse, release_still
    from production.formats.economic_reel.artefact.source import source_meta

    try:
        target = str(path.resolve())
    except OSError:
        target = str(path)
    meta = source_meta(path)
    release_still(target, _content_key(path))
    if str(meta.get("provider") or "") == "reuse":
        release_noted_reuse(target, str(meta.get("reuse_key") or ""))


def _diverse(path: Path, beat_index: int | None = None) -> bool:
    from production.formats.economic_reel.artefact.diversity import claim_diverse
    from production.formats.economic_reel.artefact.reuse import reuse_key_for

    return claim_diverse(path, source_key=reuse_key_for(path), beat_index=beat_index)


def _score(verdict: dict) -> float | None:
    try:
        return float(verdict.get("relevance_score"))
    except (TypeError, ValueError):
        return None


def _finish_log(
    beat_index: int,
    history: list[RungAttempt],
    fail_reel: bool,
    beat_spent: float,
    *,
    kept: bool,
) -> None:
    logger.info(
        "LADDER | beat=%s | history=%s | fail_reel=%s | kept=%s | "
        "spent_beat=%.4f | spent_reel=%.4f",
        int(beat_index) + 1,
        history_text(history),
        str(fail_reel).lower(),
        str(kept).lower(),
        beat_spent,
        reel_spent(),
    )
