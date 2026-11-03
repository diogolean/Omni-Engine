# -*- coding: utf-8 -*-
"""Mutable bindings for one variant-worker run."""
from __future__ import annotations


class VariantState:
    """Names the worker used to keep as locals.

    An attribute exists only after a stage assigns it. Reading a
    missing attribute fails the same way an unbound local did.
    ``getattr(state, name, default)`` replaces ``locals().get``.
    """
