"""Compatibility shim. The implementation is products.aiwake.animator.vision."""
from __future__ import annotations

from products.aiwake.animator import vision as _impl


def __getattr__(attr: str):
    return getattr(_impl, attr)
