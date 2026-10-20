# -*- coding: utf-8 -*-
"""Compatibility shim. The implementation is :mod:`shared.memory.config`."""
from __future__ import annotations

import sys

import shared.memory.config as _config

sys.modules[__name__] = _config
