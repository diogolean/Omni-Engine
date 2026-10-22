"""Compatibility shim. This package lives in production.pipeline.agentic."""
from __future__ import annotations

import importlib

_impl = importlib.import_module("production.pipeline.agentic")


def __getattr__(name: str):
    return getattr(_impl, name)
