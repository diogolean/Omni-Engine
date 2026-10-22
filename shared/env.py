# -*- coding: utf-8 -*-
"""Typed environment getters.

One copy of the small helpers that used to be repeated per module. They read
``os.environ`` only. Loading ``.env`` stays with each entrypoint (``config.py``,
``pinterest_engine.config``, and :func:`load_project_dotenv` for
``shared.paths``). Aiwake keeps its own settings.
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


def load_project_dotenv(env_path: Path) -> None:
    """Load one ``.env`` without clobbering keys already in the process.

    ``OMNI_SKIP_DOTENV`` skips the load. Entrypoints that call ``load_dotenv``
    themselves (often with ``override=True``) are unchanged.
    """
    if env_bool("OMNI_SKIP_DOTENV"):
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    if env_path.is_file():
        load_dotenv(dotenv_path=env_path, override=False, encoding="utf-8-sig")


def load_factory_dotenv(env_path: Path) -> tuple[Path, bool]:
    """Load the factory ``.env`` over process keys, keeping a preset page.

    This is the root ``config`` bootstrap (``override=True``). A page already
    in ``ACTIVE_PAGE`` wins over the file. ``load_project_dotenv`` stays
    non-clobbering and still honors ``OMNI_SKIP_DOTENV``; this loader does not,
    matching the historical ``config`` import.
    """
    resolved = env_path.expanduser().resolve()
    preserved_page = (os.environ.get("ACTIVE_PAGE") or "").strip()
    try:
        from dotenv import load_dotenv
    except ImportError:
        return resolved, False
    if not resolved.is_file():
        return resolved, False
    loaded = bool(load_dotenv(dotenv_path=resolved, override=True, encoding="utf-8-sig"))
    if preserved_page:
        os.environ["ACTIVE_PAGE"] = preserved_page
    return resolved, loaded
