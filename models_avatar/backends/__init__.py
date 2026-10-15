# -*- coding: utf-8 -*-
"""Backend selection.

``MODELS_AVATAR_BACKEND`` defaults to ``runpod_pod``. That is independent of
``REMOTE_GPU_MODE``, whose factory default is serverless. Avatar jobs need
the live ComfyUI pod until the serverless image is updated.
"""
from __future__ import annotations

from models_avatar.backends.base import GenerationBackend, GenerationJob, GenerationResult
from models_avatar.backends.runpod_pod import RunPodPodBackend
from models_avatar.backends.runpod_serverless import RunPodServerlessBackend
from models_avatar.backends.stubs import CivitaiOnsiteBackend, GeminiImageBackend
from models_avatar.env import backend_name
from models_avatar.errors import GenerationError

__all__ = [
    "GenerationBackend",
    "GenerationJob",
    "GenerationResult",
    "RunPodPodBackend",
    "RunPodServerlessBackend",
    "CivitaiOnsiteBackend",
    "GeminiImageBackend",
    "get_backend",
]


def get_backend(name: str | None = None, **kwargs: object) -> GenerationBackend:
    key = (name or backend_name()).strip().lower()
    if key in {"runpod_pod", "pod", "comfyui"}:
        return RunPodPodBackend(**kwargs)  # type: ignore[arg-type]
    if key in {"runpod_serverless", "serverless"}:
        return RunPodServerlessBackend(**kwargs)  # type: ignore[arg-type]
    if key in {"civitai", "civitai_onsite"}:
        return CivitaiOnsiteBackend()
    if key in {"gemini", "gemini_image"}:
        return GeminiImageBackend()
    raise GenerationError(
        f"Unknown models_avatar backend {key!r}. "
        "Use runpod_pod, runpod_serverless, civitai_onsite, or gemini_image."
    )
