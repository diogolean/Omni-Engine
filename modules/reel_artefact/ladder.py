# -*- coding: utf-8 -*-
"""Per-beat fallback ladder. Stop at the first still the strict judge accepts."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from modules.reel_artefact.budget import (
    beat_budget_usd,
    nano_cap,
    nano_used,
    reel_spent,
    refund,
    try_spend,
)
from modules.reel_artefact.pricing import FLUX_USD, NANOBANANA_MODELS, price_for_model
from modules.reel_qa.relevance import verdict_passed

logger = logging.getLogger(__name__)

_DEFAULT = ("real", "nb-ref", "nb-text", "flux", "reuse")
_NANO = 0.0336
_SCENE = ("flux", "flux", "reuse")

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
    """Object-beat order. Flux Dev stays off: the A/B lost to Schnell."""
    raw = (os.getenv("REEL_LADDER") or "").strip()
    if raw:
        names = [part.strip() for part in raw.split(",") if part.strip()]
    else:
        names = list(_DEFAULT)
    if "flux-dev" in names:
        logger.info("LADDER | flux-dev dropped | Schnell won the A/B")
        names = [name for name in names if name != "flux-dev"]
    return names or list(_DEFAULT)


def rungs_for(kind: str) -> list[str]:
    """Scene beats never climb the artefact-photo rungs."""
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
            from modules.reel_artefact.source import source_meta

            model = str(source_meta(path).get("model_id") or "")
        if model and price_for_model(model) > 0.034:
            return "image_nanobanana"
        return "image_nanobanana_lite"
    if rung == "flux":
        return "image_flux_schnell"
    if rung == "real":
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
) -> LadderResult:
    """Climb until one still passes. Do not lower the 0.70 judge."""
    del entity_id
    folder = Path(dest_dir)
    ctx = RungContext(
        beat_index=int(beat_index),
        narration=narration or "",
        prompt=prompt or "",
        dest=folder / "ladder.jpg",
        kind=kind or "object",
        n_beats=max(0, int(n_beats or 0)),
    )
    calls = producers or {}
    if checkpoint is not None:
        saved = checkpoint.approved_beat(int(beat_index))
        if saved and Path(str(saved.get("path") or "")).is_file():
            path = Path(str(saved["path"]))
            from modules.reel_artefact.reuse import remember_approved

            remember_approved(path, kind=str(saved.get("kind") or kind or "object"))
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

    history: list[RungAttempt] = []
    spend: dict[str, int] = {}
    beat_spent = 0.0
    saw_scored_fail = False
    last_verdict: dict = {}
    last_path: Path | None = None
    order = rungs_for(ctx.kind)
    budget = beat_budget_usd()

    for attempt, rung in enumerate(order):
        ctx.attempt = attempt
        ctx.dest = folder / f"ladder_{int(beat_index):02d}_{rung}_{attempt:02d}.jpg"
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
            history.append(RungAttempt(
                rung=rung, status="fail", reason=type(exc).__name__, cost_usd=cost,
            ))
            logger.info(
                "LADDER | beat=%s | rung=%s | status=fail | cost=%.4f | %s",
                int(beat_index) + 1, rung, cost, type(exc).__name__,
            )
            continue
        path = Path(made) if made else None
        if path is None or not path.is_file():
            if charged:
                refund(cost)
                beat_spent = max(0.0, beat_spent - cost)
                charged = False
            history.append(RungAttempt(rung=rung, status="fail", reason="missing"))
            continue
        if charged and rung in {"nb-ref", "nb-text"} and _already_paid(path):
            refund(cost)
            beat_spent = max(0.0, beat_spent - cost)
            charged = False
        if charged:
            key = _bill_key(rung, path)
            if key:
                spend[key] = spend.get(key, 0) + 1
        elif rung in {"real", "reuse"}:
            key = _bill_key(rung, path)
            if key:
                spend[key] = spend.get(key, 0) + 1
        last_path = path
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
            first = not saw_scored_fail
            _finish_log(beat_index, history + [
                RungAttempt(rung=rung, status="pass", path=str(path), cost_usd=cost),
            ], False, beat_spent, kept=False)
            return LadderResult(
                path=path,
                passed=True,
                rung=rung,
                history=history + [RungAttempt(
                    rung=rung, status="pass", path=str(path), cost_usd=cost,
                )],
                verdict=verdict,
                first_pass=first if scored else False,
                score=_score(verdict),
                reason="",
                spend=spend,
                kind=ctx.kind,
            )
        if scored:
            saw_scored_fail = True
        history.append(RungAttempt(
            rung=rung,
            status="fail",
            reason=ctx.rejection or "rejected",
            cost_usd=cost if charged else 0.0,
            path=str(path),
        ))
        logger.info(
            "LADDER | beat=%s | rung=%s | status=fail | cost=%.4f | %s",
            int(beat_index) + 1,
            rung,
            cost if charged else 0.0,
            ctx.rejection or "rejected",
        )

    kept_path, kept = _keep_compatible(ctx, last_path, history)
    fail = not kept
    _finish_log(beat_index, history, fail, beat_spent, kept=kept)
    return LadderResult(
        path=kept_path,
        passed=False,
        rung="kept" if kept else "",
        history=history,
        verdict=last_verdict,
        fail_reel=fail,
        kept=kept,
        first_pass=False,
        score=_score(last_verdict),
        reason=str(last_verdict.get("rejection_reason") or "ladder_exhausted"),
        spend=spend,
        kind=ctx.kind,
    )


def _reuse_block(ctx: RungContext) -> str:
    from modules.reel_artefact.budget import reuse_room
    from modules.reel_artefact.reuse import approved_still, reuse_key_for

    origin = approved_still(ctx.kind)
    if origin is None:
        return "none"
    allowed, reason = reuse_room(reuse_key_for(origin), ctx.n_beats)
    if not allowed:
        return reason or "cap"
    return ""


def _already_paid(path: Path) -> bool:
    from modules.reel_artefact.source import source_meta

    provider = str(source_meta(path).get("provider") or "")
    return provider in {
        "reuse", "commons", "met", "smithsonian", "openverse", "europeana", "wikimedia",
    }


def _keep_compatible(
    ctx: RungContext,
    last_path: Path | None,
    history: list[RungAttempt],
) -> tuple[Path | None, bool]:
    """Hold a compatible still only when the cap still allows it.

    A scene beat will not keep an artefact photograph. A copy the judge just
    rejected is published so one beat does not drop the reel, and it is not
    saved as a new approval. Past the cap, the reel fails closed.
    """
    from modules.reel_artefact.reuse import approved_still, reuse_into

    origin = approved_still(ctx.kind)
    if origin is None:
        return None, False
    if history and history[-1].rung == "reuse" and history[-1].status == "fail":
        if last_path is not None and last_path.is_file():
            return last_path, True
        return None, False
    if _reuse_block(ctx):
        return None, False
    copied = reuse_into(
        ctx.dest,
        ctx.beat_index,
        source=origin,
        kind=ctx.kind,
        n_beats=ctx.n_beats,
    )
    if copied is None or not Path(copied).is_file():
        return None, False
    return Path(copied), True


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
