"""Temporary shim: moved to ``core.publishing.pinterest_drip``. Removed in PR-O4."""
import sys as _sys

import core.publishing.pinterest_drip as _mod

_sys.modules[__name__] = _mod
