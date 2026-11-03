"""Compatibility shim. This package lives in production.qa.judges.visual_critic."""
from __future__ import annotations


def __getattr__(name: str):
    import importlib

    impl = importlib.import_module("production.qa.judges.visual_critic")
    return getattr(impl, name)
