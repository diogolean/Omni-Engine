# -*- coding: utf-8 -*-
"""Compatibility shim. The implementation is :mod:`shared.memory.ancient`."""
from __future__ import annotations

import sys

import shared.memory.ancient as _ancient

sys.modules[__name__] = _ancient
