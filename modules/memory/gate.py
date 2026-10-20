# -*- coding: utf-8 -*-
"""Compatibility shim. The implementation is :mod:`shared.memory.gate`."""
from __future__ import annotations

import sys

import shared.memory.gate as _gate

sys.modules[__name__] = _gate
