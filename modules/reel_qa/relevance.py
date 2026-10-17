# -*- coding: utf-8 -*-
"""Spoken-line relevance. Fail closed when every vision backend fails."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from modules.reel_qa.config import gemini_model, model_chain, relevance_threshold

_LOG = logging.getLogger(__name__)

_GATE_PROMPT = """You are a visual relevance judge. Compare the image to the spoken beat.
Return STRICT JSON only — no markdown, no commentary:
{{
  "is_relevant": boolean,
  "relevance_score": float,
  "detected_subjects": [string],
  "rejection_reason": string
}}
relevance_score is 0.0–1.0. rejection_reason must be null when relevance_score >= {threshold:.2f}.
Spoken beat: {chunk_text}
Image prompt: {prompt}
Does the picture show the subject, object, or action named in the spoken beat?
"""


def _unavailable(reason: str) -> dict[str, Any]:
    return {
        "is_relevant": False,
        "relevance_score": 0.0,
        "detected_subjects": [],
        "rejection_reason": reason,
        "qa_status": "unavailable",
        "publishable": False,
        "hard_reject": False,
        "banned_hits": [],
        "provider": None,
        "model_id": None,
    }


def _provider(name: str):
    from agents.media.providers.visual_eval import resolve_visual_eval_provider

    key = (name or "").strip().lower()
    if key == "gemini":
        return resolve_visual_eval_provider(provider="gemini", model_id=gemini_model())
    if key in {"qwen", "openrouter", "deepseek"}:
        return resolve_visual_eval_provider(provider="qwen")
    return resolve_visual_eval_provider(provider=key)


def _verdict_from_payload(
    data: dict[str, Any],
    *,
    image_path: Path,
    chunk_text: str,
    banned_subjects: list[str] | None,
    provider: str,
    model_id: str,
) -> dict[str, Any]:
    from modules.reel_visual_qa import apply_banned_subject_gate

    threshold = relevance_threshold()
    score = float(data.get("relevance_score") or 0.0)
    score = max(0.0, min(1.0, score))
    relevant = bool(data.get("is_relevant")) and score >= threshold
    reason = data.get("rejection_reason")
    if score >= threshold and relevant:
        reason = None
    elif not reason:
        reason = "below_relevance_threshold"
    subjects = data.get("detected_subjects") or []
    if not isinstance(subjects, list):
        subjects = [str(subjects)]
    verdict = apply_banned_subject_gate(
        {
            "is_relevant": relevant,
            "relevance_score": score,
            "detected_subjects": [str(s) for s in subjects][:12],
            "rejection_reason": reason,
        },
        spoken_text=chunk_text,
        banned_subjects=banned_subjects,
    )
    verdict["qa_status"] = "scored"
    verdict["publishable"] = bool(verdict.get("is_relevant")) and not verdict.get("hard_reject")
    verdict["provider"] = provider
    verdict["model_id"] = model_id
    _LOG.info(
        "VisualQA | file=%s provider=%s model=%s relevant=%s score=%.2f reason=%s",
        image_path.name,
        provider,
        model_id,
        verdict.get("is_relevant"),
        score,
        verdict.get("rejection_reason"),
    )
    return verdict


def evaluate_chunk_relevance(
    image_path: Path | str,
    current_chunk_text: str,
    image_generation_prompt: str = "",
    banned_subjects: list[str] | None = None,
    *,
    providers: list[str] | None = None,
) -> dict[str, Any]:
    """Score one still against one spoken beat.

    Tries ``REEL_QA_MODEL_CHAIN`` (default Qwen via OpenRouter, then Gemini Flash).
    A missing key, a down provider, or unparseable output is not a pass.
    """
    from modules.reel_visual_qa import _strip_json

    path = Path(image_path)
    if not path.is_file():
        return _unavailable("missing_image")

    chain = list(providers or model_chain())
    threshold = relevance_threshold()
    prompt = _GATE_PROMPT.format(
        threshold=threshold,
        chunk_text=(current_chunk_text or "").strip()[:400],
        prompt=(image_generation_prompt or "").strip()[:400],
    )
    errors: list[str] = []
    for name in chain:
        provider = _provider(name)
        if provider is None:
            errors.append(f"{name}_unavailable")
            _LOG.warning("VisualQA provider %s unavailable — trying next", name)
            continue
        try:
            result = provider.evaluate(prompt, [path], labels=["candidate"])
            data = _strip_json(getattr(result, "text", "") or "")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}:{type(exc).__name__}")
            _LOG.warning("VisualQA %s failed on %s (%s)", name, path.name, exc)
            continue
        model_id = str(getattr(result, "model_id", "") or "")
        provider_name = str(getattr(result, "provider", "") or name)
        return _verdict_from_payload(
            data,
            image_path=path,
            chunk_text=current_chunk_text,
            banned_subjects=banned_subjects,
            provider=provider_name,
            model_id=model_id,
        )

    reason = "qa_unavailable:" + ",".join(errors or ["no_provider"])
    _LOG.error("VisualQA fail-closed | file=%s | %s", path.name, reason)
    return _unavailable(reason)


def verdict_passed(verdict: dict[str, Any] | None) -> bool:
    row = verdict or {}
    if row.get("qa_status") == "unavailable" or row.get("hard_reject"):
        return False
    try:
        score = float(row.get("relevance_score") or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    return bool(row.get("is_relevant")) and score >= relevance_threshold()
