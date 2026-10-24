# -*- coding: utf-8 -*-
"""Shim package until M14. Lofi scripts live in tools.lofi."""
from __future__ import annotations


def __getattr__(name: str):
    import importlib

    return getattr(importlib.import_module("tools.lofi"), name)
