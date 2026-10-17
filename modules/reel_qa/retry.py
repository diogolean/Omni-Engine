# -*- coding: utf-8 -*-
"""Generate a still and retry with a tighter prompt. Never borrow another image."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from modules.reel_qa.config import max_attempts
from modules.reel_qa.models import StillGateResult
from modules.reel_qa.prompts import extract_site, named_entities, refine_prompt
from modules.reel_qa.relevance import (
    evaluate_chunk_relevance,
    evaluate_with_technical_retry,
    verdict_passed,
)

_LOG = logging.getLogger(__name__)

GenerateFn = Callable[..., Path | str | None]


def _remember(
    channel: str,
    topic: str,
    spoken: str,
    prompt: str,
    passed: bool,
    verdict: dict[str, Any],
    first_pass: bool | None,
) -> None:
    try:
        from modules.reel_grounding.memory import remember
        from modules.reel_grounding.prompt import primary_subject

        score = verdict.get("relevance_score")
        try:
            score_f = float(score) if score is not None else None
        except (TypeError, ValueError):
            score_f = None
        remember(
            channel=channel or "ancient_knowledge",
            topic=topic or spoken,
            spoken=spoken,
            visual_subject=primary_subject(prompt) or prompt[:240],
            passed=passed,
            score=score_f,
            rejection=str(verdict.get("rejection_reason") or ""),
            first_pass=first_pass,
        )
    except Exception:  # noqa: BLE001
        return


def _blocked(
    *,
    path: Path | None,
    extra: int,
    reason: str,
    attempts: list[dict[str, Any]],
    verdict: dict[str, Any] | None = None,
    first_pass: bool | None = None,
) -> StillGateResult:
    _LOG.error(
        "REEL QA BLOCK | reason=%s attempts=%d path=%s",
        reason,
        len(attempts),
        path.name if path else "(none)",
    )
    print(
        f"[VisualQA] BLOCK publish | reason={reason} attempts={len(attempts)}",
        flush=True,
    )
    return StillGateResult(
        path=path if path is not None and path.is_file() else None,
        extra_generations=extra,
        publishable=False,
        blocked_reason=reason,
        attempts=attempts,
        verdict=dict(verdict or {}),
        first_pass=first_pass,
    )


def generate_and_gate(
    generate_fn: GenerateFn,
    *,
    prompt: str,
    chunk_text: str,
    output_stem: str,
    output_directory: Path | str | None = None,
    channel: str = "ancient_knowledge",
    search_dirs: list[Path] | None = None,
    generate_kwargs: dict[str, Any] | None = None,
    banned_subjects: list[str] | None = None,
    domain_anchors: str = "",
    prefer_stem: str = "",
    max_attempts_n: int | None = None,
    evaluate_fn: Callable[..., dict[str, Any]] | None = None,
    episode_subject: str = "",
) -> StillGateResult:
    """Generate a still, score it, and retry up to N times.

    Each retry names the spoken sentence plus any proper noun or site.
    Unrelated cached stills and black placeholders are not used. If the
    judge cannot run, or every attempt misses, the result is not publishable.
    """
    del search_dirs, prefer_stem  # kept so existing callers stay valid
    limit = max(1, int(max_attempts_n or max_attempts()))
    judge = evaluate_fn or evaluate_chunk_relevance
    kwargs = dict(generate_kwargs or {})
    kwargs.setdefault("avatar_mode", "OFF")
    if output_directory is not None:
        kwargs["output_directory"] = output_directory

    entities = named_entities(chunk_text, domain_anchors, prompt)
    site = extract_site(chunk_text, domain_anchors, prompt)
    attempts: list[dict[str, Any]] = []
    extra = 0
    last_path: Path | None = None
    last_verdict: dict[str, Any] = {}
    reason = "not_generated"
    current_prompt = prompt or chunk_text
    first_pass: bool | None = None
    scored_seen = False

    for attempt in range(limit):
        if attempt:
            current_prompt = refine_prompt(
                prompt or chunk_text,
                chunk_text,
                entities=entities,
                site=site,
                rejection_reason=str(last_verdict.get("rejection_reason") or reason),
                attempt=attempt,
                episode_subject=episode_subject,
            )
        stem = output_stem if attempt == 0 else f"{output_stem}_r{attempt + 1:02d}"
        call_kwargs = dict(kwargs)
        call_kwargs["output_stem"] = stem
        if attempt and banned_subjects:
            prior_neg = str(call_kwargs.get("negative_prompt") or "")
            extra_neg = ", ".join(str(s) for s in banned_subjects[:12])
            call_kwargs["negative_prompt"] = (
                f"{prior_neg}, {extra_neg}" if prior_neg else extra_neg
            )
        path: Path | None = None
        try:
            result = generate_fn(current_prompt, **call_kwargs)
            path = Path(result) if result else None
        except Exception as exc:  # noqa: BLE001
            reason = f"generate_failed:{type(exc).__name__}"
            _LOG.warning("VisualQA generate attempt %d failed (%s)", attempt + 1, exc)
            attempts.append(
                {
                    "attempt": attempt + 1,
                    "prompt": current_prompt,
                    "qa_status": "generate_failed",
                    "rejection_reason": reason,
                    "relevance_score": 0.0,
                    "is_relevant": False,
                }
            )
            continue
        if attempt:
            extra += 1
        if path is None or not path.is_file():
            reason = "generate_missing_file"
            attempts.append(
                {
                    "attempt": attempt + 1,
                    "prompt": current_prompt,
                    "qa_status": "generate_failed",
                    "rejection_reason": reason,
                    "relevance_score": 0.0,
                    "is_relevant": False,
                }
            )
            continue
        last_path = path
        verdict = evaluate_with_technical_retry(
            judge,
            path,
            chunk_text,
            current_prompt,
            banned_subjects=banned_subjects,
        )
        verdict = dict(verdict or {})
        verdict["attempt"] = attempt + 1
        verdict["prompt"] = current_prompt
        last_verdict = verdict
        attempts.append(verdict)
        if verdict.get("qa_status") == "scored" and not scored_seen:
            first_pass = verdict_passed(verdict)
            scored_seen = True
            _LOG.info(
                "REEL QA FIRST PASS | stem=%s hit=%s score=%s",
                output_stem,
                first_pass,
                verdict.get("relevance_score"),
            )
        if verdict.get("qa_status") == "unavailable":
            _remember(
                channel, episode_subject, chunk_text, current_prompt, False, verdict, None,
            )
            return _blocked(
                path=last_path,
                extra=extra,
                reason=str(verdict.get("rejection_reason") or "qa_unavailable"),
                attempts=attempts,
                verdict=verdict,
                first_pass=first_pass,
            )
        if verdict_passed(verdict):
            _LOG.info(
                "VisualQA PASS | stem=%s attempt=%d score=%s",
                output_stem,
                attempt + 1,
                verdict.get("relevance_score"),
            )
            _remember(
                channel, episode_subject, chunk_text, current_prompt, True, verdict, first_pass,
            )
            return StillGateResult(
                path=path,
                extra_generations=extra,
                publishable=True,
                blocked_reason="",
                attempts=attempts,
                verdict=verdict,
                first_pass=first_pass,
            )
        reason = str(verdict.get("rejection_reason") or "below_relevance_threshold")
        _LOG.warning(
            "VisualQA REJECT | stem=%s attempt=%d/%d score=%s reason=%s",
            output_stem,
            attempt + 1,
            limit,
            verdict.get("relevance_score"),
            reason,
        )

    _remember(
        channel, episode_subject, chunk_text, current_prompt, False, last_verdict, first_pass,
    )
    return _blocked(
        path=last_path,
        extra=extra,
        reason=reason,
        attempts=attempts,
        verdict=last_verdict,
        first_pass=first_pass,
    )
