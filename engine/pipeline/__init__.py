"""Compatibility shim. This package lives in production.pipeline.stages."""
from __future__ import annotations

import importlib

_impl = importlib.import_module("production.pipeline.stages")


def __getattr__(name: str):
    return getattr(_impl, name)
