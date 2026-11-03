"""Compatibility shim. The implementation is products.aiwake.utils."""
from __future__ import annotations

from products.aiwake import utils as _impl


def __getattr__(attr: str):
    return getattr(_impl, attr)
