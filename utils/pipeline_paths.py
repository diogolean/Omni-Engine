# -*- coding: utf-8 -*-
"""Compatibility shim. The implementation is :mod:`shared.paths`."""
from __future__ import annotations

import sys

from shared import paths as _paths

sys.modules[__name__] = _paths
