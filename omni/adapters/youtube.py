"""Temporary shim: moved to ``core.publishing.adapters.youtube``. Removed in PR-O4."""
import sys as _sys

import core.publishing.adapters.youtube as _mod

_sys.modules[__name__] = _mod
