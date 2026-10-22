# -*- coding: utf-8 -*-
"""Compatibility shim. The implementation is :mod:`shared.retry`."""
from __future__ import annotations

import sys

from shared import retry as _retry

sys.modules[__name__] = _retry
