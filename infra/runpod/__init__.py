# -*- coding: utf-8 -*-
"""Compatibility shim. The package is :mod:`deploy.runpod` until M14."""
from __future__ import annotations

import sys

import deploy.runpod as _runpod

sys.modules[__name__] = _runpod
