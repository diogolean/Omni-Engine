# -*- coding: utf-8 -*-
"""Environment access for models_avatar.

Values come from the process environment. ``utils.pipeline_paths`` already
loads the factory ``.env`` without overriding exported variables. This module
reads the same keys the rest of OmniEngine uses for RunPod and output paths,
plus the avatar-specific keys documented in ``.env.example``.
"""
from __future__ import annotations

import os

from shared.paths import outputs_path


def _raw(name: str) -> str:
    return (os.getenv(name) or "").strip().strip('"').strip("'")


def env_float(name: str, default: float, *fallbacks: str) -> float:
    for key in (name, *fallbacks):
        raw = _raw(key)
        if raw:
            return float(raw)
    return default


def env_int(name: str, default: int, *fallbacks: str) -> int:
    for key in (name, *fallbacks):
        raw = _raw(key)
        if raw:
            return int(raw)
    return default


def pod_base_url() -> str:
    """Live ComfyUI base URL.

    ``MODELS_AVATAR_POD_URL`` wins. Otherwise reuse the factory ComfyUI URL
    (``REMOTE_GPU_BASE_URL`` or ``COMFYUI_BASE_URL``). Fragments are stripped
    the same way ``config._strip_url_fragment`` strips browser workflow ids.
    """
    raw = (
        _raw("MODELS_AVATAR_POD_URL")
        or _raw("REMOTE_GPU_BASE_URL")
        or _raw("COMFYUI_BASE_URL")
    )
    return raw.split("#", 1)[0].strip().rstrip("/")


def runpod_api_key() -> str:
    return _raw("RUNPOD_API_KEY")


def runpod_endpoint_id() -> str:
    explicit = _raw("RUNPOD_ENDPOINT_ID")
    if explicit:
        return explicit
    url = _raw("RUNPOD_ENDPOINT_URL").split("#", 1)[0].strip().rstrip("/")
    marker = "/v2/"
    if marker in url:
        tail = url.split(marker, 1)[1]
        return tail.split("/", 1)[0]
    return ""


def backend_name() -> str:
    return (_raw("MODELS_AVATAR_BACKEND") or "runpod_pod").lower()


def civitai_token() -> str:
    return _raw("CIVITAI_API_TOKEN")


def poll_interval_s() -> float:
    return env_float(
        "MODELS_AVATAR_POLL_INTERVAL_S",
        2.0,
        "REMOTE_GPU_POLL_INTERVAL_S",
    )


def generation_timeout_s() -> float:
    return env_float(
        "MODELS_AVATAR_TIMEOUT_S",
        600.0,
        "REMOTE_GPU_TIMEOUT_S",
    )


def min_interval_s() -> float:
    return env_float("MODELS_AVATAR_MIN_INTERVAL_S", 1.0)


def models_dir_override() -> str:
    return _raw("MODELS_AVATAR_MODELS_DIR")


def output_media_dir(*parts: str):
    """``OUTPUT_PATH/models_avatar/<parts>`` via the factory path helper."""
    return outputs_path("models_avatar", *parts)
