"""Temporary shim: moved to ``core.publishing.publisher``. Removed in PR-O4."""
import sys as _sys

import core.publishing.publisher as _mod

_sys.modules[__name__] = _mod
