# -*- coding: utf-8 -*-
"""Compatibility shim. The implementation is :mod:`shared.memory.wsl`."""
from __future__ import annotations

import sys

import shared.memory.wsl as _wsl

sys.modules[__name__] = _wsl
