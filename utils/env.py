# -*- coding: utf-8 -*-
"""Typed environment getters shared by the engine.

One copy of the small helpers that used to be repeated per module. They read
``os.environ`` only; loading ``.env`` stays with each entrypoint (``config.py``,
``pinterest_engine.config`` ...). Aiwake keeps its own settings on purpose.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

_TRUTHY = ("1", "true", "yes", "on")


def env_bool(name: str, default: bool = False) -> bool:
    """``1/true/yes/on`` (case-insensitive) is True; unset returns ``default``."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUTHY


def as_bool(value: Any, *, default: bool = False) -> bool:
    """Coerce a YAML/JSON value: ``None`` → ``default``, strings use ``env_bool`` rules."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in _TRUTHY
    return bool(value)


def env_float(name: str, default: float) -> float:
    """Float from env; unset, blank or unparsable returns ``default``."""
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def parse_interval_hours(name: str, default: float) -> float:
    """Hours from env; empty or unparsable returns ``default``."""
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return float(raw.strip())
    except ValueError:
        return default


def resolve_path(value: str | None, default: Path) -> Path:
    """``value`` (or ``default`` when empty) as an expanded ``Path``."""
    return Path((value or str(default))).expanduser()
