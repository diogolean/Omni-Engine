# -*- coding: utf-8 -*-
"""Run one generation to a clip under OUTPUT_PATH and write the cost sidecar."""
from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Callable

from modules.video_providers.base import VideoProvider
from modules.video_providers.config import ChannelVideoSettings, load_channel_settings
from modules.video_providers.errors import (
    VideoDisabledError,
    VideoError,
    VideoProviderError,
    VideoRequestError,
)
from modules.video_providers.registry import get_provider
from modules.video_providers.types import ClipResult, UsageSnapshot, VideoJob, VideoRequest
from utils.pipeline_paths import outputs_root, page_clips_dir

_LOG = logging.getLogger(__name__)
_TERMINAL = frozenset({"completed", "failed"})


def sidecar_path(dest: Path) -> Path:
    """``clip.mp4`` -> ``clip.video.json`` beside the file."""
    return Path(dest).with_suffix(".video.json")


def clip_stem(prompt: str, *, now: float | None = None) -> str:
    """Filename stem with no characters that Windows rejects."""
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now if now is not None else time.time()))
    slug = re.sub(r"[^A-Za-z0-9]+", "-", prompt).strip("-").lower()[:48]
    return f"{stamp}-{slug or 'clip'}"


def resolve_clip_path(channel: str, prompt: str, output: str | None = None) -> Path:
    """Place the mp4 under ``{OUTPUT_PATH}/{channel}/clips``."""
    if output:
        raw = Path(output)
        dest = raw if raw.is_absolute() else page_clips_dir(channel, create=True) / raw
    else:
        dest = page_clips_dir(channel, create=True) / f"{clip_stem(prompt)}.mp4"
    if dest.suffix.lower() != ".mp4":
        dest = dest.with_suffix(".mp4")
    return _under_outputs(dest)


def _under_outputs(dest: Path) -> Path:
    root = outputs_root().resolve()
    resolved = dest.resolve()
    if resolved != root and root not in resolved.parents:
        raise VideoRequestError(f"Clip path must stay under OUTPUT_PATH ({root}).")
    return dest


def generate_clip(
    request: VideoRequest,
    *,
    dest: Path,
    channel: str = "endless_summer_paradise",
    provider: VideoProvider | None = None,
    settings: ChannelVideoSettings | None = None,
    force: bool = False,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    timeout_s: float | None = None,
    min_interval_s: float | None = None,
) -> ClipResult:
    """Validate, submit, poll, download, and write the sidecar.

    Channel config stays off until enabled. ``force`` is for the explicit CLI
    and still validates the request before any credit spend.
    """
    resolved_settings = settings or load_channel_settings(channel)
    if not resolved_settings.enabled and not force:
        raise VideoDisabledError(
            f"Video generation is off for {resolved_settings.channel}. "
            "Set VIDEO_ENABLED=true or the channel prefix (for example ESP_VIDEO_ENABLED=true)."
        )
    active = provider or get_provider(
        resolved_settings.provider,
        model=resolved_settings.model,
        allow_seedance=resolved_settings.allow_seedance,
    )
    active.capabilities(request)
    target = _under_outputs(Path(dest))
    _assert_free(target)
    timeout = float(timeout_s if timeout_s is not None else resolved_settings.poll_timeout_s)
    interval = float(
        min_interval_s if min_interval_s is not None else resolved_settings.poll_min_interval_s
    )
    started = clock()
    before = active.usage_snapshot()
    params = active.public_params(request)
    record: dict[str, object] = {
        "provider": active.name,
        "model": getattr(active, "model", resolved_settings.model),
        "params": params,
        "job_id": None,
        "status": "error",
        "failure_reason": None,
        "retryable": None,
        "error": None,
        "output": None,
    }
    pending: VideoError | None = None
    output_path: Path | None = None
    try:
        job = active.submit(request)
        record["job_id"] = job.id
        record["status"] = job.status
        _LOG.info(
            "video submitted provider=%s model=%s job=%s",
            active.name,
            record["model"],
            job.id,
        )
        job = wait_for_terminal(
            active,
            job.id,
            sleep=sleep,
            clock=clock,
            timeout_s=timeout,
            min_interval_s=interval,
        )
        record["status"] = job.status
        record["failure_reason"] = job.failure_reason
        record["retryable"] = job.retryable
        if job.status == "failed":
            raise VideoProviderError(
                job.error_message or f"generation failed ({job.failure_reason or 'unknown'})",
                retryable=bool(job.retryable),
                job_id=job.id,
                failure_reason=job.failure_reason,
            )
        if job.status != "completed":
            raise VideoProviderError(
                f"generation ended in status {job.status or 'unknown'}.",
                retryable=False,
                job_id=job.id,
            )
        output_path = active.download(job, target)
        record["status"] = "completed"
        record["output"] = output_path.name
        record["retryable"] = False
    except VideoError as exc:
        pending = exc
        record["error"] = str(exc)
        record["failure_reason"] = record.get("failure_reason") or exc.failure_reason
        record["retryable"] = exc.retryable
        record["job_id"] = record.get("job_id") or exc.job_id
        if exc.failure_reason == "timeout":
            record["status"] = "timeout"
        elif record.get("status") != "failed":
            record["status"] = "error"
    except OSError as exc:
        pending = VideoProviderError(
            str(exc),
            retryable=False,
            job_id=_as_str(record.get("job_id")),
        )
        record["error"] = str(exc)
        record["retryable"] = False
        record["status"] = "error"

    after, after_error = _safe_usage(active)
    if after_error:
        record["usage_after_error"] = after_error
    elapsed = round(float(clock() - started), 3)
    side = _write_sidecar(target, record, before=before, after=after, elapsed_s=elapsed)
    if pending is not None:
        raise pending
    assert output_path is not None
    before_credits = before.credits if before else None
    after_credits = after.credits if after else None
    delta = _delta(before_credits, after_credits)
    return ClipResult(
        path=str(output_path),
        sidecar_path=str(side),
        provider=str(record["provider"]),
        model=str(record["model"]),
        job_id=str(record["job_id"] or ""),
        elapsed_s=elapsed,
        credits_before=before_credits,
        credits_after=after_credits,
        credits_delta=delta,
    )


def wait_for_terminal(
    provider: VideoProvider,
    job_id: str,
    *,
    sleep: Callable[[float], None],
    clock: Callable[[], float],
    timeout_s: float,
    min_interval_s: float,
) -> VideoJob:
    """Poll until completed or failed. Never faster than 10 seconds."""
    caps = provider.capabilities()
    floor = max(float(min_interval_s), float(caps.min_poll_interval_s), 10.0)
    interval = floor
    deadline = clock() + float(timeout_s)
    while True:
        if clock() >= deadline:
            raise VideoProviderError(
                f"Video generation {job_id} timed out after {timeout_s}s.",
                retryable=True,
                job_id=job_id,
                failure_reason="timeout",
            )
        try:
            job = provider.poll(job_id)
        except VideoProviderError as exc:
            if not exc.retryable:
                if exc.job_id is None:
                    exc.job_id = job_id
                raise
            _sleep_or_timeout(
                sleep, clock, deadline, interval, job_id=job_id, timeout_s=timeout_s
            )
            interval = _grow(interval, caps.poll_backoff, caps.poll_max_interval_s, floor)
            continue
        if job.status in _TERMINAL:
            return job
        _sleep_or_timeout(
            sleep, clock, deadline, interval, job_id=job_id, timeout_s=timeout_s
        )
        interval = _grow(interval, caps.poll_backoff, caps.poll_max_interval_s, floor)


def _grow(interval: float, backoff: float, cap: float, floor: float) -> float:
    grown = interval * (backoff if backoff > 1 else 1.5)
    return min(max(grown, floor), cap if cap > 0 else 60.0)


def _sleep_or_timeout(
    sleep: Callable[[float], None],
    clock: Callable[[], float],
    deadline: float,
    delay: float,
    *,
    job_id: str,
    timeout_s: float,
) -> None:
    # A shorter sleep would poll faster than the vendor allows, so the wait
    # ends as a timeout instead of squeezing in one more request.
    if clock() + delay > deadline:
        raise VideoProviderError(
            f"Video generation {job_id} timed out after {timeout_s}s.",
            retryable=True,
            job_id=job_id,
            failure_reason="timeout",
        )
    sleep(delay)


def _assert_free(dest: Path) -> None:
    side = sidecar_path(dest)
    paths = (
        dest,
        side,
        dest.with_name(dest.name + ".part"),
        side.with_name(side.name + ".part"),
    )
    for path in paths:
        if path.exists():
            raise FileExistsError(f"Refusing to overwrite {path}")


def _safe_usage(provider: VideoProvider) -> tuple[UsageSnapshot | None, str | None]:
    try:
        return provider.usage_snapshot(), None
    except VideoError as exc:
        return None, str(exc)


def _delta(before: int | None, after: int | None) -> int | None:
    if before is None or after is None:
        return None
    return int(after) - int(before)


def _as_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _write_sidecar(
    dest: Path,
    record: dict[str, object],
    *,
    before: UsageSnapshot | None,
    after: UsageSnapshot | None,
    elapsed_s: float,
) -> Path:
    before_credits = before.credits if before else None
    after_credits = after.credits if after else None
    payload = {
        "provider": record["provider"],
        "model": record["model"],
        "params": record["params"],
        "job_id": record["job_id"],
        "status": record["status"],
        "failure_reason": record["failure_reason"],
        "retryable": record["retryable"],
        "error": record["error"],
        "output": record["output"],
        "credits_before": before_credits,
        "credits_after": after_credits,
        "credits_delta": _delta(before_credits, after_credits),
        "credits_unit": (before or after).unit if (before or after) else "character_count",
        "credits_note": (
            "credits_before and credits_after are character_count from "
            "GET /v1/user/subscription. ElevenLabs does not publish a video price; "
            "the delta is the measured usage for this clip."
        ),
        "elapsed_s": elapsed_s,
        "subscription_before": before.fields if before else None,
        "subscription_after": after.fields if after else None,
        "tier_before": before.tier if before else None,
        "tier_after": after.tier if after else None,
        "usage_after_error": record.get("usage_after_error"),
    }
    path = sidecar_path(dest)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"
    _write_exclusive(path, encoded)
    return path


def _write_exclusive(path: Path, data: bytes) -> None:
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite {path}")
    part = path.with_name(path.name + ".part")
    if part.exists():
        raise FileExistsError(f"Refusing to overwrite {part}")
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    fd = os.open(os.fspath(part), flags, 0o644)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    if path.exists():
        os.unlink(os.fspath(part))
        raise FileExistsError(f"Refusing to overwrite {path}")
    os.replace(os.fspath(part), os.fspath(path))
