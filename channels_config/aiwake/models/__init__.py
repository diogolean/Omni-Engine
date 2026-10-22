"""Compatibility shim. The implementation is products.aiwake.models."""
from __future__ import annotations

from products.aiwake import models as _impl


def __getattr__(attr: str):
    return getattr(_impl, attr)
