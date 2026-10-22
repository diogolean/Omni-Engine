"""Product-agnostic animation package.

The parametric engine is :mod:`animation.parametric`. The public facade is
:mod:`animation.api`. Channel and show code does not live here.
"""
from __future__ import annotations

__all__ = ["api"]


def __getattr__(name: str):
    if name == "api":
        from animation import api as _api

        return _api
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
