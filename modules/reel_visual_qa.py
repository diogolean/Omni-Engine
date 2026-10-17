# -*- coding: utf-8 -*-
"""
Reel still QA.

Spoken-line relevance (Qwen, then Gemini Flash) lives in ``modules.reel_qa``
and fails closed: a down judge, a missing key, or an unparseable reply does
not count as a match. ``generate_and_gate`` retries with the narration
sentence and its named entity or site. It does not borrow an unrelated
cached still or write a black placeholder.

The aesthetic critic helpers below (``critique_still``, ``apply_act_vision_qa``)
are unchanged and are not the publish gate.
"""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

_LOG = logging.getLogger(__name__)

QUALITY_THRESHOLD: float = 5.5
MAX_RETRIES: int = 1  # one fallback → r01 + r02 only
_CRITIC_WORKERS: int = 3

GenerateFn = Callable[..., Path | str | None]


def _threshold() -> float:
    try:
        from quality.VisualQA_Agent.visual_critic import ancient_mystery_quality_threshold

        return float(ancient_mystery_quality_threshold(QUALITY_THRESHOLD))
    except Exception:
        try:
            from agents.orchestrator.criteria import QUALITY_THRESHOLD as _AK_T

            return float(_AK_T)
        except Exception:
            return QUALITY_THRESHOLD


def _shift_prompt(prompt: str, attempt: int) -> str:
    try:
        from agents.orchestrator.criteria import CAMERA_REWRITE_CYCLE

        cam = CAMERA_REWRITE_CYCLE[int(attempt) % len(CAMERA_REWRITE_CYCLE)]
    except Exception:
        cam = (
            "CAMERA REWRITE: wide aerial, open sky, subject centred, "
            "no interior frames."
        )
    return f"{prompt} {cam}".strip()


def critique_still(
    image_path: Path | str,
    *,
    channel: str = "ancient_knowledge",
) -> tuple[float, bool, str]:
    """Return ``(score, passed, feedback)``. Never raises."""
    path = Path(image_path)
    threshold = _threshold()
    if not path.is_file():
        return 0.0, False, "missing_image"
    try:
        from quality.VisualQA_Agent.channel_rag import (
            get_channel_rules,
            seed_default_channels,
            set_channel_context,
        )
        from quality.VisualQA_Agent.visual_critic import evaluate_image

        seed_default_channels(force=False)
        set_channel_context(channel)
        rules = dict(get_channel_rules(channel) or {})
        rules["critic_profile"] = rules.get("critic_profile") or "ancient_mystery"
        verdict = evaluate_image(
            path,
            channel_name=channel,
            rules=rules,
            quality_threshold=threshold,
        )
        score = float(getattr(verdict, "score", 0.0) or 0.0)
        passed = bool(getattr(verdict, "passed", False)) and score >= threshold
        feedback = str(getattr(verdict, "fix_instructions", "") or "").strip()
        if not passed and not feedback:
            feedback = "; ".join(getattr(verdict, "flaws", None) or []) or "below threshold"
        return score, passed, feedback
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("reel VisualQA failed on %s (%s) — keeping still.", path.name, exc)
        return 0.0, False, f"critic_exception:{type(exc).__name__}"


def apply_act_vision_qa(
    *,
    acts: list[dict[str, Any]],
    generate_fn: GenerateFn,
    channel: str = "ancient_knowledge",
    output_directory: Path | str | None = None,
    cost_tracker: Any | None = None,
    max_workers: int = _CRITIC_WORKERS,
) -> tuple[list[Path], int]:
    """
    Critique each r01 still; regenerate failures once (r02); commit best score.

    ``acts`` items: ``path``, ``prompt``, ``stem`` (output stem without _r01/_r02).
    Returns ``(committed_paths, extra_image_generations)``.
    """
    threshold = _threshold()
    workers = max(1, int(max_workers))
    n = len(acts)
    if n == 0:
        return [], 0

    scores: list[float] = [0.0] * n
    feedbacks: list[str] = [""] * n
    paths: list[Path] = []
    for item in acts:
        raw = item.get("path")
        paths.append(Path(raw) if raw else Path())

    def _critique_one(idx: int) -> tuple[int, float, bool, str]:
        score, passed, feedback = critique_still(paths[idx], channel=channel)
        if cost_tracker is not None:
            try:
                cost_tracker.track_text("text_gemini_flash", char_count=600)
            except Exception:
                pass
        return idx, score, passed, feedback

    _LOG.info(
        "REEL VisualQA | n=%d | threshold=%.2f | max_retries=%d | profile=ancient_mystery",
        n, threshold, MAX_RETRIES,
    )
    with ThreadPoolExecutor(max_workers=min(workers, n)) as pool:
        futs = [pool.submit(_critique_one, i) for i in range(n)]
        for fut in as_completed(futs):
            idx, score, passed, feedback = fut.result()
            scores[idx] = score
            feedbacks[idx] = feedback
            _LOG.warning(
                "REEL QA r01 | act=%d/%d score=%.2f passed=%s threshold=%.2f",
                idx + 1, n, score, passed, threshold,
            )

    retry_idx = [i for i in range(n) if scores[i] < threshold]
    extra_imgs = 0
    if not retry_idx or MAX_RETRIES < 1:
        if retry_idx:
            _LOG.warning(
                "REEL QA BEST-SCORE FALLBACK | %d act(s) below %.2f — committing r01, no r03",
                len(retry_idx), threshold,
            )
        return paths, extra_imgs

    out_dir = Path(output_directory) if output_directory else None

    def _regen_one(idx: int) -> tuple[int, Path | None]:
        prompt = _shift_prompt(str(acts[idx].get("prompt") or ""), attempt=1)
        stem = str(acts[idx].get("stem") or f"act{idx + 1:02d}")
        if not stem.endswith("_r02"):
            stem = f"{stem}_r02"
        try:
            kwargs: dict[str, Any] = {
                "output_stem": stem,
                "avatar_mode": "OFF",
            }
            if out_dir is not None:
                kwargs["output_directory"] = out_dir
            result = generate_fn(prompt, **kwargs)
            return idx, Path(result) if result else None
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("REEL QA r02 generate failed act=%d (%s)", idx + 1, exc)
            return idx, None

    r02_paths: dict[int, Path] = {}
    with ThreadPoolExecutor(max_workers=min(workers, len(retry_idx))) as pool:
        futs = [pool.submit(_regen_one, i) for i in retry_idx]
        for fut in as_completed(futs):
            idx, new_path = fut.result()
            if new_path is not None and new_path.is_file():
                r02_paths[idx] = new_path
                extra_imgs += 1

    def _critique_r02(idx: int) -> tuple[int, float, bool, str]:
        score, passed, feedback = critique_still(r02_paths[idx], channel=channel)
        if cost_tracker is not None:
            try:
                cost_tracker.track_text("text_gemini_flash", char_count=600)
            except Exception:
                pass
        return idx, score, passed, feedback

    if r02_paths:
        with ThreadPoolExecutor(max_workers=min(workers, len(r02_paths))) as pool:
            futs = [pool.submit(_critique_r02, i) for i in r02_paths]
            for fut in as_completed(futs):
                idx, score, passed, feedback = fut.result()
                _LOG.warning(
                    "REEL QA r02 | act=%d/%d score=%.2f (r01=%.2f) passed=%s",
                    idx + 1, n, score, scores[idx], passed,
                )
                if score >= scores[idx] and r02_paths[idx].is_file():
                    paths[idx] = r02_paths[idx]
                    scores[idx] = score
                    feedbacks[idx] = feedback

    below = sum(1 for s in scores if s < threshold)
    if below:
        _LOG.warning(
            "REEL QA BEST-SCORE FALLBACK | %d/%d act(s) still < %.2f — "
            "committing highest score, no r03",
            below, n, threshold,
        )
    return paths, extra_imgs


# ---------------------------------------------------------------------------
# Qwen Vision spoken-relevance gate (mandatory before compile)
# ---------------------------------------------------------------------------

RELEVANCE_THRESHOLD: float = 0.70


def apply_banned_subject_gate(
    verdict: dict[str, Any],
    *,
    spoken_text: str = "",
    banned_subjects: list[str] | None = None,
) -> dict[str, Any]:
    """
    Hard-reject only when a detected subject is on the *topic-specific*
    negative list and the spoken window never named it.

    ``banned_subjects`` must be derived from the active script domain
    (e.g. Greek → pyramid/egyptian/pharaoh). Empty list = no landmark ban.
    """
    out = dict(verdict or {})
    spoken = (spoken_text or "").lower()
    subjects = [str(s).lower() for s in (out.get("detected_subjects") or [])]
    hits: list[str] = []
    for banned in banned_subjects or []:
        token = str(banned).strip().lower()
        if not token or token in spoken:
            continue
        token_re = re.compile(rf"\b{re.escape(token)}\b")
        for subject in subjects:
            if token_re.search(subject):
                hits.append(token)
                break
    if hits:
        unique = list(dict.fromkeys(hits))
        reason = f"HARD_REJECT: Detected out-of-scope element: {unique[0]}"
        out["is_relevant"] = False
        out["rejection_reason"] = reason
        out["hard_reject"] = True
        out["banned_hits"] = unique
    else:
        out.setdefault("hard_reject", False)
        out.setdefault("banned_hits", [])
    return out


def rebuild_prompt_after_reject(
    prompt: str,
    chunk_text: str,
    *,
    domain_anchors: str = "",
    banned_subjects: list[str] | None = None,
) -> str:
    """Recalculate a first-principles retry prompt after a hard reject."""
    anchors = (domain_anchors or "").strip()
    spoken = (chunk_text or "").strip() or (prompt or "").strip()[:240]
    head = f"{anchors}. " if anchors else ""
    return (
        f"{head}Photoreal documentary still of the spoken beat: {spoken}. "
        "Single historically accurate subject. "
        "Keep the setting inside the spoken geography only."
    ).strip()


def _strip_json(text: str) -> dict[str, Any]:
    import json
    import re

    raw = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.IGNORECASE)
    if fence:
        raw = fence.group(1).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        raw = raw[start : end + 1]
    raw = re.sub(r",\s*([}\]])", r"\1", raw)
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("VisualQA JSON was not an object")
    return data


def evaluate_chunk_relevance(
    image_path: Path | str,
    current_chunk_text: str,
    image_generation_prompt: str = "",
    banned_subjects: list[str] | None = None,
) -> dict[str, Any]:
    """Fail-closed spoken relevance. See ``modules.reel_qa.relevance``."""
    from modules.reel_qa.relevance import evaluate_chunk_relevance as _eval

    return _eval(
        image_path,
        current_chunk_text,
        image_generation_prompt,
        banned_subjects=banned_subjects,
    )


def resolve_cached_channel_background(
    *,
    channel: str,
    dest: Path,
    search_dirs: list[Path] | None = None,
    prefer_stem: str = "",
) -> Path:
    """Copy a prior same-topic still, or write a solid placeholder.

    The production gate does not call this. ``generate_and_gate`` fails closed
    instead of substituting an unrelated still or a black frame. Kept so older
    tests can still describe the historical helper.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    prefix = (prefer_stem or "").strip().lower()
    dest_name = dest.name.lower()
    for folder in search_dirs or []:
        try:
            hits = sorted(
                [
                    p
                    for p in Path(folder).rglob("*")
                    if p.is_file()
                    and p.suffix.lower() in {".png", ".jpg", ".jpeg"}
                    and p.stat().st_size > 2048
                    and "fallback" not in p.name.lower()
                    and p.name.lower() != dest_name
                ],
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            hits = []
        if prefix:
            hits = [p for p in hits if prefix in p.name.lower()]
        else:
            hits = []
        for hit in hits[:20]:
            try:
                import shutil

                shutil.copy2(hit, dest)
                _LOG.warning(
                    "VisualQA fallback | channel=%s using cached still %s → %s",
                    channel, hit.name, dest.name,
                )
                return dest
            except OSError:
                continue
    try:
        from PIL import Image as _PILImage

        _PILImage.new("RGB", (1080, 1920), (12, 10, 8)).save(str(dest))
        _LOG.warning("VisualQA fallback | solid placeholder → %s", dest)
    except Exception as exc:  # noqa: BLE001
        _LOG.error("VisualQA fallback placeholder failed (%s)", exc)
    return dest


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
    evaluate_fn: Any | None = None,
    episode_subject: str = "",
):
    """Retry a still against its spoken line. Fail closed. See ``modules.reel_qa.retry``.

    Iterates as ``(path, extra_generations)``. ``publishable`` is on the result.
    """
    from modules.reel_qa.retry import generate_and_gate as _gate

    return _gate(
        generate_fn,
        prompt=prompt,
        chunk_text=chunk_text,
        output_stem=output_stem,
        output_directory=output_directory,
        channel=channel,
        search_dirs=search_dirs,
        generate_kwargs=generate_kwargs,
        banned_subjects=banned_subjects,
        domain_anchors=domain_anchors,
        prefer_stem=prefer_stem,
        max_attempts_n=max_attempts_n,
        evaluate_fn=evaluate_fn,
        episode_subject=episode_subject,
    )
