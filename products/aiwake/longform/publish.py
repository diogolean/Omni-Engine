# -*- coding: utf-8 -*-
"""Assemble the YouTube package after the renderer returns."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace
from pathlib import Path

from products.aiwake.longform.cost import estimate_episode_cost
from products.aiwake.longform.metadata import build_youtube_package, chapter_marks
from products.aiwake.longform.plan import EpisodePlan
from products.aiwake.longform.qa import evaluate_package, probe_media
from products.aiwake.longform.shorts import cut_or_regenerate_shorts
from products.aiwake.pipeline import PipelineResult
from products.aiwake.settings import AiwakeSettings

_LOG = logging.getLogger("aiwake.longform")


def finalize_longform(
    result: PipelineResult,
    plan: EpisodePlan,
    settings: AiwakeSettings,
    *,
    offline: bool,
    output_dir: Path,
    orchestrator: str,
    target: str,
    shorts_count: int | None = None,
    require_media: bool = True,
) -> PipelineResult:
    """Write chapters, cut the teasers, and run the pre-publish gate."""
    output_dir.mkdir(parents=True, exist_ok=True)
    preset = settings.long_format
    count = int(shorts_count if shorts_count is not None else preset.shorts_count)
    count = max(2, min(3, count))
    existing = _read_json(result.metadata_path)
    boundaries = _exchange_starts(existing.get("timestamps"))
    duration_hint = _float(existing.get("duration_s")) or plan.target_duration_s
    long_probe = probe_media(result.video_path) if result.video_path else None
    if long_probe and long_probe.duration_s:
        duration_hint = long_probe.duration_s
    chapters = chapter_marks(plan, boundaries_s=boundaries, duration_s=duration_hint)
    topic = result.transcript.topic
    package = build_youtube_package(
        topic=topic,
        orchestrator=orchestrator,
        target=target,
        plan=plan,
        chapters=chapters,
    )
    cost = estimate_episode_cost(
        plan,
        preset,
        orchestrator=orchestrator,
        target=target,
        offline=offline,
    )
    battle_audio = _battle_audio(output_dir, result.transcript.session_id)
    shorts_dir = output_dir / "shorts"
    clips = []
    if require_media:
        try:
            clips = cut_or_regenerate_shorts(
                master=result.video_path,
                battle_audio=battle_audio,
                output_dir=shorts_dir,
                plan=plan,
                chapters=chapters,
                preset=preset,
                long_title=package.title,
                topic=topic,
                duration_s=duration_hint,
                count=count,
            )
        except Exception as exc:  # noqa: BLE001 — a teaser failure must not drop the episode
            _LOG.error("short derivation failed: %s", exc)
            clips = []
    short_probes = [probe_media(clip.path) for clip in clips] if require_media else []
    report = evaluate_package(
        long_probe=long_probe,
        short_probes=short_probes,
        preset=preset,
        target_s=plan.target_duration_s,
        shorts_expected=count,
        require_media=require_media,
    )
    metadata_path = output_dir / "metadata.json"
    payload = dict(existing)
    payload.update(
        {
            "session_id": result.transcript.session_id,
            "post_type": "long_format",
            "title": package.title,
            "description": package.description,
            "tags": package.tags,
            "chapters": package.to_dict()["chapters"],
            "resolution": [plan.resolution[0], plan.resolution[1]],
            "target_duration_s": plan.target_duration_s,
            "topic": topic,
            "video": None if result.video_path is None else result.video_path.name,
            "models": {"orchestrator": orchestrator, "target": target},
            "episode_plan": plan.to_dict(),
            "cost_estimate": cost.to_dict(),
            "shorts": [clip.metadata | {"path": str(clip.path)} for clip in clips],
            "qa": report.to_dict(),
        }
    )
    metadata_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    if clips:
        (shorts_dir / "manifest.json").write_text(
            json.dumps([clip.metadata for clip in clips], indent=2) + "\n",
            encoding="utf-8",
        )
    return replace(
        result,
        metadata_path=metadata_path,
        shorts=tuple(clip.path for clip in clips),
        qa_passed=report.passed,
        cost_estimate_usd=cost.usd,
        qa_failures=tuple(report.failures),
    )


def _battle_audio(output_dir: Path, session_id: str) -> Path | None:
    candidate = output_dir / f"{session_id}_battle_audio.wav"
    if candidate.is_file():
        return candidate
    return None


def _exchange_starts(timestamps: object) -> list[float] | None:
    if not isinstance(timestamps, list) or not timestamps:
        return None
    starts: list[float] = []
    for index in range(0, len(timestamps), 2):
        item = timestamps[index]
        if not isinstance(item, dict) or "start" not in item:
            return None
        starts.append(float(item["start"]))
    return starts or None


def _read_json(path: Path | None) -> dict:
    if path is None or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
