"""Compatibility shim. This package lives in production.formats.lofi."""
from __future__ import annotations

import importlib

_impl = importlib.import_module("production.formats.lofi")


def __getattr__(name: str):
    return getattr(_impl, name)
