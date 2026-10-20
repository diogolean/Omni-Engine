# -*- coding: utf-8 -*-
"""Compatibility shim. The implementation is :mod:`shared.memory.similarity`."""
from __future__ import annotations

import sys

import shared.memory.similarity as _similarity

sys.modules[__name__] = _similarity
