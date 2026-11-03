"""Compatibility shim. This package lives in production.pipeline.agentic.agents."""
from __future__ import annotations

import importlib

_impl = importlib.import_module("production.pipeline.agentic.agents")


def __getattr__(name: str):
    return getattr(_impl, name)
