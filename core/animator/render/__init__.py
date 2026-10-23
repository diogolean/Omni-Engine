"""Compatibility shim. The implementation is products.aiwake.animator.render."""
from __future__ import annotations

from products.aiwake.animator import render as _impl


def __getattr__(attr: str):
    return getattr(_impl, attr)
