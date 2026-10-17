# -*- coding: utf-8 -*-
"""Shared request and result types for every video backend."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ReferenceImage:
    """A local path or an already-uploaded reference.

    ``source`` is a filesystem path, ``asset:<asset_id>`` (POST /v1/assets),
    or ``generation:<generation_id>`` from an earlier Flows generation.
    ``role`` is ``subject`` or ``style`` (Veo). Other backends ignore role
    only when it is ``subject``.
    """

    source: str
    role: str = "subject"


@dataclass(frozen=True)
class VideoRequest:
    """What a channel asks a provider to render.

    ``audio`` is the generate-audio flag. ``None`` omits the field so the
    provider default applies (Veo defaults to generating audio). A reference
    audio file is not part of this request; lip-sync models are not enabled.
    """

    prompt: str
    negative_prompt: str | None = None
    duration: float | None = None
    aspect: str | None = None
    resolution: str | None = None
    audio: bool | None = None
    start_frame: str | None = None
    end_frame: str | None = None
    reference_images: tuple[ReferenceImage, ...] = ()
    seed: int | None = None


@dataclass(frozen=True)
class ProviderCapabilities:
    """What the selected backend accepts. ``active`` is false for skeletons."""

    provider: str
    active: bool
    model: str
    models: tuple[str, ...]
    default_model: str
    quality_model: str | None
    durations_secs: tuple[int, ...]
    aspects: tuple[str, ...]
    resolutions: tuple[str, ...]
    max_reference_images: int
    supports_negative_prompt: bool
    supports_seed: bool
    supports_generate_audio: bool
    supports_start_end_frame: bool
    reference_requires_duration: int | None
    min_poll_interval_s: float
    poll_backoff: float
    poll_max_interval_s: float
    notes: str = ""


@dataclass(frozen=True)
class VideoJob:
    """One generation, as returned by submit or poll."""

    id: str
    status: str
    provider: str
    model: str
    content_url: str | None = None
    content_mime_type: str | None = None
    failure_reason: str | None = None
    error_message: str | None = None
    retryable: bool | None = None


@dataclass(frozen=True)
class CostEstimate:
    """A price hint. ``None`` amounts mean the vendor has no published rate."""

    provider: str
    model: str
    credits: float | None
    usd: float | None
    unit: str
    note: str
    usd_low: float | None = None
    usd_high: float | None = None


@dataclass(frozen=True)
class UsageSnapshot:
    """Usage meter taken from GET /v1/user/subscription.

    ElevenLabs does not publish a video price table. ``character_count`` is
    the usage counter that endpoint documents, so the sidecar stores it as
    the credit reading and keeps any extra scalar fields whose names contain
    ``credit`` in case the payload grows a dedicated balance.
    """

    credits: int | None
    unit: str
    tier: str | None
    fields: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ClipResult:
    """A finished clip and the sidecar that records what it cost."""

    path: str
    sidecar_path: str
    provider: str
    model: str
    job_id: str
    elapsed_s: float
    credits_before: int | None
    credits_after: int | None
    credits_delta: int | None
