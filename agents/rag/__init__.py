"""Compatibility shim. This package lives in production.writers.rag."""
from __future__ import annotations


def __getattr__(name: str):
    import importlib

    impl = importlib.import_module("production.writers.rag")
    return getattr(impl, name)
