"""Compatibility shim. The implementation is products.aiwake.media."""
from __future__ import annotations

from products.aiwake import media as _impl


def __getattr__(attr: str):
    return getattr(_impl, attr)
