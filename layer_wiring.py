# -*- coding: utf-8 -*-
"""Composition root for the production / distribution seam.

Not a layer. ``shared.layer_bridge`` loads modules through here so
import-linter does not draw an edge from one layer into the other.
Callers pass a canonical module name. This file does not import either
layer at import time.
"""
from __future__ import annotations

import importlib
from typing import Any

_cache: dict[str, Any] = {}
_loading: set[str] = set()


def load(module: str) -> Any:
    """Import ``module`` once and return it."""
    cached = _cache.get(module)
    if cached is not None:
        return cached
    if module in _loading:
        raise RuntimeError(f"layer_wiring cycle while loading {module}")
    _loading.add(module)
    try:
        loaded = importlib.import_module(module)
    finally:
        _loading.discard(module)
    _cache[module] = loaded
    return loaded
