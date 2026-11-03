"""Compatibility shim. Library source lives in distribution.scheduling.library_source."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("distribution.scheduling.library_source", run_name="__main__")
else:
    from distribution.scheduling import library_source as _impl

    sys.modules[__name__] = _impl
