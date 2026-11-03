# -*- coding: utf-8 -*-
"""Memory group names: ``<project>_<channel>_<purpose>``."""

from __future__ import annotations

import re

# Existing Aiwake container tags. Do not rename them.
AIWAKE_PRODUCTION_HISTORY = "aiwake_production_history"
AIWAKE_SYSTEM_RULES = "aiwake_system_rules"

_SEGMENT = re.compile(r"[^A-Za-z0-9]+")
_NAME = re.compile(r"[A-Za-z0-9_:-]+")


def _segment(label: str, value: str) -> str:
    slug = _SEGMENT.sub("_", (value or "").strip()).strip("_").lower()
    if not slug:
        raise ValueError(f"{label} is required")
    return slug


def memory_namespace(project: str, channel: str, purpose: str) -> str:
    """Build a container tag ``<project>_<channel>_<purpose>``.

    Supermemory allows letters, digits, underscore, colon, and hyphen, and
    at most 100 characters.
    """
    name = "_".join(
        (
            _segment("project", project),
            _segment("channel", channel),
            _segment("purpose", purpose),
        )
    )
    if len(name) > 100 or not _NAME.fullmatch(name):
        raise ValueError(f"namespace {name!r} is not a valid container tag")
    return name
