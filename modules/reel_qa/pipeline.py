# -*- coding: utf-8 -*-
"""Seal a compiled reel: still scores + MP4 gate + JSON report."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from modules.reel_qa.metrics import first_pass_summary, format_first_pass
from modules.reel_qa.mp4_gate import cues_from_beats, cues_from_word_timings, gate_final_mp4
from modules.reel_qa.relevance import evaluate_chunk_relevance, verdict_passed
from modules.reel_qa.report import new_report, report_targets, write_qa_report

_LOG = logging.getLogger(__name__)


def _score_beats(
    beats: list[dict[str, Any]],
    *,
    evaluate_fn: Callable[..., dict[str, Any]] | None,
    score_stills: bool,
) -> list[str]:
    if not score_stills:
        return []
    judge = evaluate_fn or evaluate_chunk_relevance
    reasons: list[str] = []
    for beat in beats:
        if beat.get("passed") is True and beat.get("qa_status") != "unavailable":
            continue
        image = str(beat.get("image_path") or "")
        text = str(beat.get("text") or beat.get("spoken_text") or "")
        if beat.get("passed") is False:
            reasons.append(str(beat.get("reason") or "beat_rejected"))
            continue
        if not image or not Path(image).is_file():
            beat["passed"] = False
            beat["reason"] = beat.get("reason") or "missing_still"
            reasons.append("missing_still")
            continue
        try:
            verdict = judge(image, text, text) or {}
        except Exception as exc:  # noqa: BLE001
            verdict = {
                "is_relevant": False,
                "relevance_score": 0.0,
                "qa_status": "unavailable",
                "rejection_reason": f"qa_unavailable:{type(exc).__name__}",
            }
        passed = verdict_passed(verdict) and verdict.get("qa_status") != "unavailable"
        beat["score"] = verdict.get("relevance_score")
        beat["passed"] = bool(passed)
        beat["reason"] = verdict.get("rejection_reason") or ""
        beat["qa_status"] = verdict.get("qa_status") or "scored"
        beat["provider"] = verdict.get("provider")
        if not passed:
            reasons.append(str(beat.get("reason") or "beat_rejected"))
    return reasons


def _fail_mp4(reason: str) -> dict[str, Any]:
    return {
        "passed": False,
        "reasons": [reason],
        "duration_s": 0.0,
        "has_audio": False,
        "mean_volume_db": None,
        "max_volume_db": None,
        "frames": [],
    }


def seal_compiled_reel(
    *,
    video_path: Path | str | None,
    post_type: str,
    beats: list[dict[str, Any]] | None = None,
    word_timings: list[tuple] | None = None,
    output_dir: Path | str | None = None,
    stem: str = "reel",
    channel: str = "",
    score_stills: bool = True,
    evaluate_fn: Callable[..., dict[str, Any]] | None = None,
    probe_fn: Callable[[Path], dict[str, Any]] | None = None,
    frame_fn: Callable[..., Any] | None = None,
    blocked_reason: str = "",
) -> dict[str, Any]:
    """Score beats, gate the MP4, and persist the report. Never raises."""
    rows = [dict(b) for b in (beats or [])]
    try:
        still_reasons = _score_beats(rows, evaluate_fn=evaluate_fn, score_stills=score_stills)
        video = Path(video_path) if video_path else None
        if video is not None and str(video):
            cues = cues_from_word_timings(word_timings) or cues_from_beats(rows)
            mp4 = gate_final_mp4(
                video,
                cues,
                probe_fn=probe_fn,
                evaluate_fn=evaluate_fn,
                frame_fn=frame_fn,
                work_dir=(Path(output_dir) / f"{stem}_frames") if output_dir else None,
            )
        else:
            mp4 = _fail_mp4(blocked_reason or "missing_video")
        reasons = list(still_reasons)
        if not mp4.get("passed"):
            reasons.extend(str(r) for r in (mp4.get("reasons") or []) if r)
        if blocked_reason:
            reasons.insert(0, blocked_reason)
        unique: list[str] = []
        for reason in reasons:
            if reason and reason not in unique:
                unique.append(reason)
        passed = not unique and bool(mp4.get("passed")) and not still_reasons
        verdict = "pass" if passed else "fail"
        report = new_report(
            post_type=post_type,
            video_path=str(video) if video else "",
            beats=rows,
            mp4=mp4,
            verdict=verdict,
            blocked_reason="; ".join(unique),
            channel=channel,
        )
        report["first_pass"] = first_pass_summary(rows)
    except Exception as exc:  # noqa: BLE001
        _LOG.error("REEL QA seal failed (%s) — fail closed", exc)
        report = new_report(
            post_type=post_type,
            video_path=str(video_path or ""),
            beats=rows,
            mp4=_fail_mp4(f"qa_unavailable:{type(exc).__name__}"),
            verdict="fail",
            blocked_reason=f"qa_unavailable:{type(exc).__name__}",
            channel=channel,
        )
    summary = report.get("first_pass") or first_pass_summary(rows)
    report["first_pass"] = summary
    write_qa_report(
        report,
        report_targets(video_path=video_path, output_dir=output_dir, stem=stem),
    )
    rate_line = format_first_pass(summary)
    _LOG.info(rate_line)
    print(rate_line, flush=True)
    _LOG.info(
        "REEL QA report | verdict=%s post_type=%s path=%s",
        report.get("verdict"),
        post_type,
        report.get("report_path"),
    )
    print(
        f"[VisualQA] verdict={report.get('verdict')} report={report.get('report_path')}",
        flush=True,
    )
    return report
