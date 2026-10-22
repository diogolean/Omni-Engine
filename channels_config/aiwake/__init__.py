"""Compatibility shim. The implementation is products.aiwake."""
from __future__ import annotations

from products import aiwake as _impl


def __getattr__(attr: str):
    return getattr(_impl, attr)
