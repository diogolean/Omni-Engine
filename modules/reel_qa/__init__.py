"""Compatibility shim. This package lives in production.qa."""
from __future__ import annotations


def __getattr__(name: str):
    import importlib

    impl = importlib.import_module("production.qa")
    return getattr(impl, name)
