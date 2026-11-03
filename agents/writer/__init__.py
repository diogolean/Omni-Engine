"""Compatibility shim. This package lives in production.writers."""
from __future__ import annotations


def __getattr__(name: str):
    import importlib

    impl = importlib.import_module("production.writers")
    return getattr(impl, name)
