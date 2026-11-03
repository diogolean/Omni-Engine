# -*- coding: utf-8 -*-
"""Unattended batch generation.

A batch writes ``plan.json`` and appends ``manifest.jsonl`` under
``{OUTPUT_PATH}/models_avatar/<avatar>/<date>/<preset>/<batch_id>/``.
Restarting with the same batch id skips manifest rows whose status is
``ok``. Transport failures are retried. Prompt-policy errors are not.
"""
from __future__ import annotations

import json
import logging
import os
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from production.providers.avatar.avatar_config import AvatarConfig, load_avatar
from production.providers.avatar.backends.base import GenerationBackend, GenerationJob
from production.providers.avatar.env import min_interval_s
from production.providers.avatar.errors import ModelsAvatarError, is_retryable
from production.providers.avatar.paths import generation_dir, today_stamp
from production.providers.avatar.prompts import default_template_path, load_templates, render_scene
from production.providers.avatar.service import prepare_job

logger = logging.getLogger(__name__)

SleepFn = Callable[[float], None]


@dataclass
class BatchReport:
    batch_dir: Path
    planned: int
    completed: int
    skipped: int
    failed: int
    files: list[str] = field(default_factory=list)


def run_batch(
    avatar_id: str,
    *,
    set_name: str,
    count: int,
    preset: str | None = None,
    batch_id: str = "batch",
    backend: GenerationBackend,
    seed_base: int | None = None,
    day: str | None = None,
    resume: bool = True,
    max_attempts: int = 3,
    interval_s: float | None = None,
    templates_path: Path | None = None,
    sleep: SleepFn | None = None,
    continue_on_error: bool = True,
) -> BatchReport:
    """Generate ``count`` images, cycling the chosen template set."""
    import time

    if count < 1:
        raise ValueError("count must be at least 1.")
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1.")
    avatar = load_avatar(avatar_id)
    spec_name = preset or avatar.default_preset
    avatar.preset(spec_name)
    library = load_templates(templates_path or default_template_path(avatar))
    templates = library.templates_for(set_name)
    pause = min_interval_s() if interval_s is None else interval_s
    sleeper = sleep or time.sleep
    stamp = day or today_stamp()
    directory = generation_dir(avatar.id, spec_name, day=stamp, batch_id=batch_id)
    directory.mkdir(parents=True, exist_ok=True)
    plan_path = directory / "plan.json"
    manifest_path = directory / "manifest.jsonl"

    if plan_path.is_file():
        if not resume:
            raise ModelsAvatarError(
                f"Batch {batch_id!r} already exists at {directory}. "
                "Use a new --batch-id or resume it."
            )
        plan = _read_json(plan_path)
        _require_same_plan(
            plan,
            avatar_id=avatar.id,
            preset=spec_name,
            set_name=set_name,
            count=count,
        )
    else:
        plan = _build_plan(
            avatar,
            templates=templates,
            library=library,
            preset=spec_name,
            set_name=set_name,
            count=count,
            seed_base=seed_base,
            day=stamp,
            batch_id=batch_id,
        )
        _write_json(plan_path, plan)

    done = _completed_indices(manifest_path) if resume else set()
    report = BatchReport(
        batch_dir=directory,
        planned=len(plan["jobs"]),
        completed=0,
        skipped=0,
        failed=0,
    )
    executed = 0
    for job_spec in plan["jobs"]:
        index = int(job_spec["index"])
        if index in done:
            report.skipped += 1
            continue
        if executed and pause > 0:
            sleeper(pause)
        executed += 1
        record = _run_one(
            avatar,
            job_spec,
            preset=spec_name,
            directory=directory,
            backend=backend,
            manifest_path=manifest_path,
            max_attempts=max_attempts,
            sleep=sleeper,
        )
        if record["status"] == "ok":
            report.completed += 1
            report.files.append(record["file"])
        else:
            report.failed += 1
            if not continue_on_error:
                raise ModelsAvatarError(record.get("error") or "batch job failed")
    logger.info(
        "models_avatar batch | avatar=%s preset=%s planned=%d completed=%d skipped=%d failed=%d dir=%s",
        avatar.id,
        spec_name,
        report.planned,
        report.completed,
        report.skipped,
        report.failed,
        directory,
    )
    return report


def _run_one(
    avatar: AvatarConfig,
    job_spec: dict[str, Any],
    *,
    preset: str,
    directory: Path,
    backend: GenerationBackend,
    manifest_path: Path,
    max_attempts: int,
    sleep: SleepFn,
) -> dict[str, Any]:
    dest = directory / f"{int(job_spec['index']):04d}_{int(job_spec['seed'])}.png"
    job: GenerationJob = prepare_job(
        avatar,
        preset=preset,
        scene=str(job_spec["scene"]),
        seed=int(job_spec["seed"]),
        dest=dest,
        filename_prefix=f"{avatar.id}_{preset}_{job_spec['index']:04d}_{job_spec['seed']}",
    )
    # The plan froze the composed prompt. Rebuild uses the same scene, so the
    # positive text matches. Keep the plan's prompt in the manifest.
    last_error = ""
    for attempt in range(1, max_attempts + 1):
        try:
            result = backend.generate(job)
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            logger.warning(
                "models_avatar retry | index=%s attempt=%s/%s error=%s",
                job_spec["index"],
                attempt,
                max_attempts,
                last_error,
            )
            if not is_retryable(exc) or attempt >= max_attempts:
                record = _record(job_spec, preset, dest, status="failed", error=last_error, backend=backend.name)
                _append_jsonl(manifest_path, record)
                return record
            sleep(min(30.0, 2.0 ** (attempt - 1)))
            continue
        record = _record(
            job_spec,
            preset,
            result.path,
            status="ok",
            error="",
            backend=result.backend,
            job_id=result.job_id,
            prompt=result.prompt,
            negative=result.negative,
        )
        _append_jsonl(manifest_path, record)
        return record
    record = _record(job_spec, preset, dest, status="failed", error=last_error, backend=backend.name)
    _append_jsonl(manifest_path, record)
    return record


def _record(
    job_spec: dict[str, Any],
    preset: str,
    dest: Path,
    *,
    status: str,
    error: str,
    backend: str,
    job_id: str = "",
    prompt: str | None = None,
    negative: str | None = None,
) -> dict[str, Any]:
    return {
        "index": job_spec["index"],
        "template_id": job_spec["template_id"],
        "set": job_spec["set"],
        "prompt": prompt if prompt is not None else job_spec["prompt"],
        "negative": negative if negative is not None else job_spec["negative"],
        "seed": job_spec["seed"],
        "preset": preset,
        "file": str(dest),
        "status": status,
        "backend": backend,
        "job_id": job_id,
        "error": error,
    }


def _build_plan(
    avatar: AvatarConfig,
    *,
    templates,
    library,
    preset: str,
    set_name: str,
    count: int,
    seed_base: int | None,
    day: str,
    batch_id: str,
) -> dict[str, Any]:
    from production.providers.avatar.prompts import compose_negative, compose_positive

    rng = random.Random(seed_base if seed_base is not None else random.SystemRandom().randrange(1, 2**31))
    jobs = []
    negative = compose_negative(avatar)
    for index in range(count):
        template = templates[index % len(templates)]
        scene = render_scene(library, template)
        positive = compose_positive(avatar, scene)
        jobs.append(
            {
                "index": index,
                "template_id": template.id,
                "set": template.set_name,
                "scene": scene,
                "prompt": positive,
                "negative": negative,
                "seed": rng.randint(1, 2**31 - 1),
            }
        )
    return {
        "avatar_id": avatar.id,
        "preset": preset,
        "set": set_name,
        "count": count,
        "created_on": day,
        "batch_id": batch_id,
        "jobs": jobs,
    }


def _require_same_plan(
    plan: dict[str, Any],
    *,
    avatar_id: str,
    preset: str,
    set_name: str,
    count: int,
) -> None:
    mismatches = []
    if plan.get("avatar_id") != avatar_id:
        mismatches.append("avatar_id")
    if plan.get("preset") != preset:
        mismatches.append("preset")
    if plan.get("set") != set_name:
        mismatches.append("set")
    if int(plan.get("count") or 0) != count:
        mismatches.append("count")
    if mismatches:
        raise ModelsAvatarError(
            "Existing batch plan does not match this run "
            f"({', '.join(mismatches)}). Choose a new --batch-id."
        )


def _completed_indices(path: Path) -> set[int]:
    done: set[int] = set()
    if not path.is_file():
        return done
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("status") == "ok":
            done.add(int(row["index"]))
    return done


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ModelsAvatarError(f"Expected an object in {path}.")
    return data


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
