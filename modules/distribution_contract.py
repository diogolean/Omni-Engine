# -*- coding: utf-8 -*-
"""Compatibility shim. The implementation is :mod:`shared.contracts`."""
from __future__ import annotations

import sys

from shared import contracts as _contracts

sys.modules[__name__] = _contracts
