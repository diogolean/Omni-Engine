"""Compatibility shim. This package lives in production.formats.economic_reel.artefact."""
from __future__ import annotations

import importlib

_impl = importlib.import_module("production.formats.economic_reel.artefact")


def __getattr__(name: str):
    return getattr(_impl, name)
