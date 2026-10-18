# -*- coding: utf-8 -*-
"""Spoken-line relevance. Fail closed when every vision backend fails."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from modules.reel_qa.config import gemini_model, model_chain, relevance_threshold

_LOG = logging.getLogger(__name__)

_GATE_PROMPT = """You are a strict documentary still judge. You are shown the IMAGE.
Judge the picture. The prompt is context and is not evidence that the picture is correct.
Return STRICT JSON only — no markdown, no commentary:
{{
  "image_description": string,
  "shows_expected_subject": boolean,
  "anachronism": boolean,
  "non_photoreal": boolean,
  "anatomy_defect": boolean,
  "setting_contradicts_beat": boolean,
  "is_relevant": boolean,
  "relevance_score": float,
  "detected_subjects": [string],
  "rejection_reason": string
}}
image_description is a literal caption of the picture: place, objects, whether it is a
photograph or CGI or a miniature or an illustration, and any anatomy you can see.
Set anachronism true for a modern, sci-fi, steampunk, or futuristic object in an ancient
or museum documentary beat, unless the spoken beat itself asks for that object.
Set non_photoreal true for CGI, a 3D render, an illustration, a cartoon, or a miniature
or clay-model look.
Set anatomy_defect true for a mangled, fused, or extra limb.
Set setting_contradicts_beat true when the place contradicts the spoken beat.
A visible expected subject is not enough. The wrong place, a non-photoreal look, or a
broken body still fails.
is_relevant is true only when every flag above is false and the picture shows the subject.
relevance_score is 0.0–1.0. rejection_reason is null only when is_relevant is true
and relevance_score >= {threshold:.2f}.
Spoken beat: {chunk_text}
Image prompt: {prompt}
The image prompt's EXPECTED SUBJECT or THIS BEAT'S OBJECT is the object this still must show.
When the spoken beat is abstract and names no object, the picture must still show that
subject, as a photoreal documentary photograph, in a setting the beat allows.
"""

_SHOT_PROMPT = """You are a strict documentary still judge. You are shown the IMAGE.
Score the picture against the PLANNED SHOT. The planner already chose this
picture for the spoken line. Do not fail the picture because the spoken line
names an object the planned shot deliberately leaves out.
Return STRICT JSON only — no markdown, no commentary:
{{
  "image_description": string,
  "shows_expected_subject": boolean,
  "anachronism": boolean,
  "non_photoreal": boolean,
  "anatomy_defect": boolean,
  "setting_contradicts_beat": boolean,
  "contains_forbidden_object": boolean,
  "is_relevant": boolean,
  "relevance_score": float,
  "detected_subjects": [string],
  "rejection_reason": string
}}
image_description is a literal caption of the picture: place, objects, whether it is a
photograph or CGI or a miniature or an illustration, and any anatomy you can see.
Set anachronism true for a modern, sci-fi, steampunk, or futuristic object, unless
the planned shot itself asks for that object.
Set non_photoreal true for CGI, a 3D render, an illustration, a cartoon, or a miniature
or clay-model look.
Set anatomy_defect true for a mangled, fused, or extra limb.
Set setting_contradicts_beat true only when the place contradicts the planned setting.
Set contains_forbidden_object true when the picture shows a named artefact, gears,
pendants, a gold ornament, a figurine, or any other specific object.
Set shows_expected_subject true when the planned subject is visible in the planned setting.
is_relevant is true only when every flag above is false and the planned subject is visible.
relevance_score is 0.0–1.0. rejection_reason is null only when is_relevant is true
and relevance_score >= {threshold:.2f}.
PLANNED SHOT
Subject: {shot_subject}
Setting: {shot_setting}
Era: {shot_era}
Spoken line, context only, not the subject: {chunk_text}
Image prompt: {prompt}
"""

# A Flux scene must not contain these. An object photograph may.
_FORBIDDEN_CAPTION = (
    "gear wheel",
    "gears",
    "gear",
    "pendant",
    "gold ornament",
    "golden ornament",
    "gold bird",
    "figurine",
    "corroded fragment",
    "bronze fragment",
)


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


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return value is True


def _caption_forbidden(description: str) -> bool:
    """True when a Flux still's caption names an object the scene must not show."""
    caption = " ".join((description or "").lower().split())
    if not caption:
        return False
    for phrase in _FORBIDDEN_CAPTION:
        if phrase in caption:
            prefix = caption[max(0, caption.find(phrase) - 16):caption.find(phrase)]
            if prefix.rstrip().endswith(("no", "not", "without", "never")):
                continue
            return True
    return False


def _verdict_from_payload(
    data: dict[str, Any],
    *,
    image_path: Path,
    chunk_text: str,
    banned_subjects: list[str] | None,
    provider: str,
    model_id: str,
    score_against: str = "narration",
) -> dict[str, Any]:
    from modules.reel_qa.documentary import documentary_failures
    from modules.reel_visual_qa import apply_banned_subject_gate

    threshold = relevance_threshold()
    score = float(data.get("relevance_score") or 0.0)
    score = max(0.0, min(1.0, score))
    subjects = data.get("detected_subjects") or []
    if not isinstance(subjects, list):
        subjects = [str(subjects)]
    subjects = [str(item) for item in subjects][:12]
    described = " ".join(
        part for part in (
            str(data.get("image_description") or ""),
            " ".join(subjects),
        ) if part
    )
    defects = documentary_failures(described, spoken=chunk_text)
    for key, label in (
        ("anachronism", "anachronism"),
        ("non_photoreal", "non_photoreal"),
        ("anatomy_defect", "anatomy"),
        ("setting_contradicts_beat", "setting_contradicts_beat"),
    ):
        if _as_bool(data.get(key)) and label not in defects:
            defects.append(label)
    if score_against == "shot" and (
        _as_bool(data.get("contains_forbidden_object")) or _caption_forbidden(described)
    ):
        if "contains_forbidden_object" not in defects:
            defects.append("contains_forbidden_object")
    relevant = bool(data.get("is_relevant")) and score >= threshold and not defects
    reason = data.get("rejection_reason")
    if defects:
        relevant = False
        score = min(score, 0.35)
        reason = "documentary_fail:" + ",".join(defects)
    elif score >= threshold and relevant:
        reason = None
    elif not reason:
        reason = "below_relevance_threshold"
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
    if _as_bool(data.get("shape_mismatch")) and "shape_mismatch" not in defects:
        defects.append("shape_mismatch")
        relevant = False
        score = min(score, 0.35)
        reason = "documentary_fail:" + ",".join(defects)
        verdict["is_relevant"] = False
        verdict["relevance_score"] = score
        verdict["rejection_reason"] = reason
    if defects:
        verdict["is_relevant"] = False
        verdict["hard_reject"] = True
        verdict["publishable"] = False
        verdict["documentary_defects"] = list(defects)
        verdict["rejection_reason"] = "documentary_fail:" + ",".join(defects)
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


def call_judge(judge: Callable[..., dict[str, Any]], image, text: str, prompt: str, **extra):
    """Call a judge with the kwargs it accepts.

    Stills and final frames share this so a test double that only takes
    ``banned_subjects`` still runs, and the real judge can receive the
    reference photograph.
    """
    import inspect

    kwargs = {key: value for key, value in extra.items() if value is not None}
    try:
        signature = inspect.signature(judge)
    except (TypeError, ValueError):
        signature = None
    if signature is not None:
        if not any(
            item.kind == inspect.Parameter.VAR_KEYWORD
            for item in signature.parameters.values()
        ):
            kwargs = {key: value for key, value in kwargs.items() if key in signature.parameters}
    return judge(image, text, prompt, **kwargs)


def evaluate_chunk_relevance(
    image_path: Path | str,
    current_chunk_text: str,
    image_generation_prompt: str = "",
    banned_subjects: list[str] | None = None,
    *,
    providers: list[str] | None = None,
    reference_image: str | Path | None = None,
    canonical_shape: str = "",
    score_against: str = "narration",
    shot_subject: str = "",
    shot_setting: str = "",
    shot_era: str = "",
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
    against = (score_against or "narration").strip().lower()
    if against == "shot":
        prompt = _SHOT_PROMPT.format(
            threshold=threshold,
            shot_subject=(shot_subject or "the planned subject").strip()[:240],
            shot_setting=(shot_setting or "the planned setting").strip()[:240],
            shot_era=(shot_era or "the planned era").strip()[:120],
            chunk_text=(current_chunk_text or "").strip()[:400],
            prompt=(image_generation_prompt or "").strip()[:800],
        )
    else:
        prompt = _GATE_PROMPT.format(
            threshold=threshold,
            chunk_text=(current_chunk_text or "").strip()[:400],
            prompt=(image_generation_prompt or "").strip()[:400],
        )
    images: list[Path] = [path]
    labels = ["candidate"]
    reference = Path(reference_image) if reference_image else None
    if reference is not None and reference.is_file():
        shape = (canonical_shape or "the object in the reference photograph").strip()
        prompt += (
            "\nA second image labeled REFERENCE is a photograph of the real artefact. "
            f"Canonical shape: {shape}. "
            'Add "shape_mismatch": boolean. Set it true when the candidate does not '
            "show that shape. relevance_score is shape fidelity against the reference.\n"
        )
        images.append(reference)
        labels.append("reference")
    errors: list[str] = []
    for name in chain:
        provider = _provider(name)
        if provider is None:
            errors.append(f"{name}_unavailable")
            _LOG.warning("VisualQA provider %s unavailable — trying next", name)
            continue
        try:
            result = provider.evaluate(prompt, images, labels=labels)
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
            score_against=against,
        )

    reason = "qa_unavailable:" + ",".join(errors or ["no_provider"])
    _LOG.error("VisualQA fail-closed | file=%s | %s", path.name, reason)
    return _unavailable(reason)


def evaluate_with_technical_retry(
    judge: Callable[..., dict[str, Any]],
    image: Path | str,
    text: str,
    prompt: str,
    banned_subjects: list[str] | None = None,
    *,
    reference_image: str | Path | None = None,
    canonical_shape: str = "",
    score_against: str = "narration",
    shot_subject: str = "",
    shot_setting: str = "",
    shot_era: str = "",
) -> dict[str, Any]:
    """Score one still. Retry the judge on timeout or an invalid reply.

    A scored rejection is returned immediately. Only ``qa_status=unavailable``
    spends the extra judge calls. The image is not regenerated here.
    """
    from modules.reel_qa.config import judge_retries

    extra = judge_retries()
    verdict: dict[str, Any] = {}
    for i in range(extra + 1):
        try:
            verdict = dict(
                call_judge(
                    judge,
                    image,
                    text,
                    prompt,
                    banned_subjects=banned_subjects,
                    reference_image=reference_image or "",
                    canonical_shape=canonical_shape,
                    score_against=score_against,
                    shot_subject=shot_subject,
                    shot_setting=shot_setting,
                    shot_era=shot_era,
                )
                or {}
            )
        except Exception as exc:  # noqa: BLE001
            verdict = {
                "is_relevant": False,
                "relevance_score": 0.0,
                "rejection_reason": f"qa_unavailable:{type(exc).__name__}",
                "qa_status": "unavailable",
                "publishable": False,
            }
        if verdict.get("qa_status") != "unavailable":
            return verdict
        if i < extra:
            _LOG.warning(
                "VisualQA technical failure (%s) — judge retry %d/%d",
                verdict.get("rejection_reason"),
                i + 1,
                extra,
            )
    return verdict


def verdict_passed(verdict: dict[str, Any] | None) -> bool:
    row = verdict or {}
    if row.get("qa_status") == "unavailable" or row.get("hard_reject"):
        return False
    try:
        score = float(row.get("relevance_score") or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    return bool(row.get("is_relevant")) and score >= relevance_threshold()
