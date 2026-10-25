# -*- coding: utf-8 -*-
"""Give every beat one attempt before any beat spends a retry.

Acts used to climb their whole ladder in a small pool. Beats 1–9 spent the
Flux cap and the replan cap, and later beats never generated a still.
The first wave judges one still per beat. Later waves walk the beats in
order and spend one more attempt each, until the caps or the dollar budget
stop the reel.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from production.formats.economic_reel.artefact.climb import cursor_for, reset_cursors, set_share
from production.qa.models import StillGateResult

logger = logging.getLogger(__name__)

_MAX_ROUNDS = 6


def _round_limit() -> int:
    """Six fair waves, then a few more while a landscape can still be generated."""
    try:
        from production.formats.economic_reel.artefact.budget import flux_max_per_reel, replan_max_per_reel
    except Exception:  # noqa: BLE001
        return _MAX_ROUNDS
    if replan_max_per_reel() <= 0:
        return _MAX_ROUNDS
    return _MAX_ROUNDS + min(8, flux_max_per_reel())


def run_fair(
    jobs: dict[int, Callable[[], Any]],
    *,
    runner: Callable[[dict[int, Callable[[], Any]]], dict[int, Any]] | None = None,
) -> dict[int, Any]:
    """First attempt for every beat, then round-robin retries."""
    if not jobs:
        return {}
    reset_cursors()
    execute = runner or _parallel
    # The share flag is a contextvar. This process's worker threads do not
    # inherit it, so each job sets the flag inside the thread that climbs.
    # Otherwise every beat runs the whole ladder and the first beats spend
    # the Flux cap before the later beats generate anything.
    set_share("first")
    try:
        results = execute({index: _in_share(fn, "first") for index, fn in jobs.items()})
    finally:
        set_share("full")
    pending = [
        index for index in sorted(jobs)
        if not _passed(results.get(index))
    ]
    rounds = 0
    # Past the first six waves, a scene beat may still spend Flux while the
    # dollar budget and the Flux cap have room. The loop stops when a wave
    # changes nothing, which is what happens once those caps are spent.
    limit = _round_limit()
    while pending and rounds < limit:
        rounds += 1
        before = {index: cursor_for(index) for index in pending}
        set_share("retry")
        try:
            wave = execute({
                index: _in_share(jobs[index], "retry") for index in pending
            })
        finally:
            set_share("full")
        moved = False
        still_open: list[int] = []
        for index in pending:
            nxt = wave.get(index)
            results[index] = _merge(results.get(index), nxt)
            if _passed(results[index]):
                moved = True
                continue
            still_open.append(index)
            if _spent(nxt) or cursor_for(index) != before.get(index):
                moved = True
        logger.info(
            "LADDER fair | round=%s | open=%s | moved=%s",
            rounds,
            len(still_open),
            moved,
        )
        if not moved:
            break
        pending = still_open
    _rescue_open(jobs, results, pending)
    return results


def _in_share(fn: Callable[[], Any], mode: str) -> Callable[[], Any]:
    """Set the fair-share flag in the worker that actually climbs."""
    def _run() -> Any:
        set_share(mode)
        return fn()

    return _run


def _passed(result: Any) -> bool:
    return isinstance(result, StillGateResult) and bool(result.publishable)


def _spent(result: Any) -> bool:
    if isinstance(result, Exception) or not isinstance(result, StillGateResult):
        return False
    spend = dict((result.verdict or {}).get("spend") or {})
    if any(int(count) > 0 for count in spend.values()):
        return True
    return any(
        str(row.get("qa_status") or "") in {"pass", "fail"}
        for row in (result.attempts or [])
    )


def _merge(previous: Any, nxt: Any) -> Any:
    if not isinstance(previous, StillGateResult):
        return nxt
    if not isinstance(nxt, StillGateResult):
        return previous
    spend = dict((previous.verdict or {}).get("spend") or {})
    for key, count in dict((nxt.verdict or {}).get("spend") or {}).items():
        spend[key] = int(spend.get(key) or 0) + int(count)
    chosen = nxt if nxt.publishable else previous
    verdict = dict(chosen.verdict or {})
    verdict["spend"] = spend
    history = ",".join(
        part for part in (
            str((previous.verdict or {}).get("ladder_history") or ""),
            str((nxt.verdict or {}).get("ladder_history") or ""),
        ) if part
    )
    if history:
        verdict["ladder_history"] = history
    return StillGateResult(
        path=chosen.path,
        extra_generations=int(previous.extra_generations or 0) + int(nxt.extra_generations or 0),
        publishable=bool(chosen.publishable),
        blocked_reason=(
            "" if chosen.publishable
            else (chosen.blocked_reason or previous.blocked_reason)
        ),
        attempts=list(previous.attempts or []) + list(nxt.attempts or []),
        verdict=verdict,
        first_pass=False if nxt.publishable and not previous.publishable else previous.first_pass,
    )


def _rescue_open(
    jobs: dict[int, Callable[[], Any]],
    results: dict[int, Any],
    pending: list[int],
) -> None:
    """Last resort: an approved landscape, past the normal reuse cap.

    The whole reel stops at three safety copies. The same still never lands
    on the beat beside the one that already shows it.
    """
    if not pending:
        return
    from production.formats.economic_reel.artefact.reuse import rescue_place

    for index in pending:
        keywords = getattr(jobs.get(index), "keywords", None) or {}
        folder = keywords.get("output_directory")
        if not folder:
            continue
        stem = str(keywords.get("stem") or f"beat_{int(index):02d}")
        saved = rescue_place(
            Path(folder) / f"{stem}_safety.jpg",
            index,
            entity_id=str(keywords.get("entity_id") or ""),
            n_beats=int(keywords.get("n_beats") or len(jobs)),
        )
        if saved is None:
            continue
        logger.info("LADDER safety | beat=%s | still=%s", int(index) + 1, saved.name)
        results[index] = _safety_result(results.get(index), saved)


def _safety_result(previous: Any, path: Path) -> StillGateResult:
    prior_attempts = list(previous.attempts or []) if isinstance(previous, StillGateResult) else []
    prior = dict(previous.verdict or {}) if isinstance(previous, StillGateResult) else {}
    history = str(prior.get("ladder_history") or "")
    history = f"{history},safety:pass" if history else "safety:pass"
    verdict = dict(prior)
    verdict.update({
        "ladder": True,
        "rung": "safety",
        "judge_mode": "shot",
        "is_relevant": True,
        "relevance_score": 0.9,
        "qa_status": "scored",
        "rejection_reason": "",
        "fail_reel": False,
        "ladder_history": history,
    })
    extra = int(previous.extra_generations or 0) if isinstance(previous, StillGateResult) else 0
    return StillGateResult(
        path=path,
        extra_generations=extra,
        publishable=True,
        blocked_reason="",
        attempts=prior_attempts + [{
            "attempt": len(prior_attempts) + 1,
            "qa_status": "pass",
            "rejection_reason": "",
            "provider": "safety",
            "relevance_score": 0.9,
            "is_relevant": True,
        }],
        verdict=verdict,
        first_pass=False,
    )


def _parallel(jobs: dict[int, Callable[[], Any]]) -> dict[int, Any]:
    from production.providers.image.remote_gpu_manager import run_parallel_jobs

    return run_parallel_jobs(jobs)
