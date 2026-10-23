"""Compatibility shim. The implementation is products.aiwake.observers."""
from __future__ import annotations

from products.aiwake import observers as _impl


def __getattr__(attr: str):
    return getattr(_impl, attr)
