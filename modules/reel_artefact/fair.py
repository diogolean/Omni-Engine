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
from typing import Any, Callable

from modules.reel_artefact.climb import cursor_for, reset_cursors, set_share
from modules.reel_qa.models import StillGateResult

logger = logging.getLogger(__name__)

_MAX_ROUNDS = 6


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
    while pending and rounds < _MAX_ROUNDS:
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
        blocked_reason="" if chosen.publishable else (chosen.blocked_reason or previous.blocked_reason),
        attempts=list(previous.attempts or []) + list(nxt.attempts or []),
        verdict=verdict,
        first_pass=False if nxt.publishable and not previous.publishable else previous.first_pass,
    )


def _parallel(jobs: dict[int, Callable[[], Any]]) -> dict[int, Any]:
    from core.remote_gpu_manager import run_parallel_jobs

    return run_parallel_jobs(jobs)
