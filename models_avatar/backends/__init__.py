"""Compatibility shim. This package lives in production.providers.avatar.backends."""
from __future__ import annotations


def __getattr__(name: str):
    import importlib

    impl = importlib.import_module("production.providers.avatar.backends")
    return getattr(impl, name)
