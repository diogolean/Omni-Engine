"""Compatibility shim. The parametric rig lives in animation.parametric.factory.

Aiwake puppet factories live in products.aiwake.animator.factory and are
re-exported by the sibling modules here until M14.
"""
from __future__ import annotations

import importlib

_impl = importlib.import_module("animation.parametric.factory")
from core.animator.aiwake_plate import apply_rigger

apply_rigger(importlib.import_module("animation.parametric.factory.rigger"))


def __getattr__(name: str):
    return getattr(_impl, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_impl)))
