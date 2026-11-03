# -*- coding: utf-8 -*-
"""Compatibility shim. The implementation is :mod:`shared.memory.namespaces`."""
from __future__ import annotations

import sys

import shared.memory.namespaces as _namespaces

sys.modules[__name__] = _namespaces
