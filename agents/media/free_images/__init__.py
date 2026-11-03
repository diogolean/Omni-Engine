"""Compatibility shim. This package lives in production.providers.image.free."""
from __future__ import annotations


def __getattr__(name: str):
    import importlib

    impl = importlib.import_module("production.providers.image.free")
    return getattr(impl, name)
