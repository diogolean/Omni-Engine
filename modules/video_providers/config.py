# -*- coding: utf-8 -*-
"""Per-channel video settings. Off unless a config flag or env var enables them.

Channel modules live at ``channels_config/<channel>/video_config.py`` and
export plain constants. Environment variables override the file. A
channel-prefixed name wins over the generic one:

    ESP_VIDEO_ENABLED=true     # this channel
    VIDEO_ENABLED=true         # any channel that does not set its own prefix

The generic names are ``VIDEO_ENABLED``, ``VIDEO_PROVIDER``, ``VIDEO_MODEL``,
``VIDEO_ALLOW_SEEDANCE``, ``VIDEO_POLL_TIMEOUT_S``, and
``VIDEO_POLL_MIN_INTERVAL_S``.
"""
from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass
from types import ModuleType

from modules.video_providers.errors import VideoRequestError
from utils.pipeline_paths import channel_slug, channels_config_root

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


@dataclass(frozen=True)
class ChannelVideoSettings:
    channel: str
    enabled: bool
    provider: str
    model: str
    allow_seedance: bool
    poll_timeout_s: float
    poll_min_interval_s: float
    env_prefix: str


def load_channel_settings(channel: str) -> ChannelVideoSettings:
    """Resolve provider, model, and the enable flag for one channel."""
    slug = channel_slug(channel)
    module = _load_video_config(slug)
    prefix = ""
    if module is not None:
        prefix = str(getattr(module, "ENV_PREFIX", "") or "").strip().strip("_")

    enabled = _env_bool(
        _names(prefix, "VIDEO_ENABLED"),
        _module_bool(module, "VIDEO_ENABLED", False),
    )
    provider = _env_str(
        _names(prefix, "VIDEO_PROVIDER"),
        _module_str(module, "VIDEO_PROVIDER", "elevenlabs"),
    ).lower()
    model = _env_str(
        _names(prefix, "VIDEO_MODEL"),
        _module_str(module, "VIDEO_MODEL", "veo-3.1-fast-generate-001"),
    )
    allow_seedance = _env_bool(
        _names(prefix, "VIDEO_ALLOW_SEEDANCE"),
        _module_bool(module, "VIDEO_ALLOW_SEEDANCE", False),
    )
    timeout_s = _env_float(
        _names(prefix, "VIDEO_POLL_TIMEOUT_S"),
        _module_float(module, "VIDEO_POLL_TIMEOUT_S", 900.0),
    )
    interval_s = _env_float(
        _names(prefix, "VIDEO_POLL_MIN_INTERVAL_S"),
        _module_float(module, "VIDEO_POLL_MIN_INTERVAL_S", 10.0),
    )
    if timeout_s < 10:
        raise VideoRequestError("VIDEO_POLL_TIMEOUT_S must be at least 10 seconds.")
    if interval_s < 10:
        raise VideoRequestError(
            "VIDEO_POLL_MIN_INTERVAL_S must be at least 10 seconds. "
            "ElevenLabs asks callers not to poll faster than that."
        )
    return ChannelVideoSettings(
        channel=slug,
        enabled=enabled,
        provider=provider,
        model=model,
        allow_seedance=allow_seedance,
        poll_timeout_s=timeout_s,
        poll_min_interval_s=interval_s,
        env_prefix=prefix,
    )


def video_enabled(channel: str) -> bool:
    return load_channel_settings(channel).enabled


def _names(prefix: str, suffix: str) -> tuple[str, ...]:
    if prefix:
        return (f"{prefix}_{suffix}", suffix)
    return (suffix,)


def _env_str(names: tuple[str, ...], default: str) -> str:
    for name in names:
        raw = os.getenv(name)
        if raw is not None and str(raw).strip() != "":
            return str(raw).strip()
    return default


def _env_bool(names: tuple[str, ...], default: bool) -> bool:
    for name in names:
        raw = os.getenv(name)
        if raw is None or str(raw).strip() == "":
            continue
        token = str(raw).strip().lower()
        if token in _TRUE:
            return True
        if token in _FALSE:
            return False
        raise VideoRequestError(
            f"{name} must be true/false (got {raw!r})."
        )
    return default


def _env_float(names: tuple[str, ...], default: float) -> float:
    for name in names:
        raw = os.getenv(name)
        if raw is None or str(raw).strip() == "":
            continue
        try:
            return float(str(raw).strip())
        except ValueError as exc:
            raise VideoRequestError(f"{name} must be a number.") from exc
    return default


def _module_str(module: ModuleType | None, name: str, default: str) -> str:
    if module is None or not hasattr(module, name):
        return default
    value = getattr(module, name)
    text = str(value).strip() if value is not None else ""
    return text or default


def _module_bool(module: ModuleType | None, name: str, default: bool) -> bool:
    if module is None or not hasattr(module, name):
        return default
    value = getattr(module, name)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        token = value.strip().lower()
        if token in _TRUE:
            return True
        if token in _FALSE:
            return False
    return bool(value)


def _module_float(module: ModuleType | None, name: str, default: float) -> float:
    if module is None or not hasattr(module, name):
        return default
    try:
        return float(getattr(module, name))
    except (TypeError, ValueError):
        return default


def _load_video_config(channel: str) -> ModuleType | None:
    path = channels_config_root() / channel / "video_config.py"
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location(f"video_config_{channel}", path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
