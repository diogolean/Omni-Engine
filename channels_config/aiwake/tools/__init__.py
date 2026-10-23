"""Compatibility shim. The implementation is products.aiwake.tools."""
from __future__ import annotations

from products.aiwake import tools as _impl


def __getattr__(attr: str):
    return getattr(_impl, attr)
