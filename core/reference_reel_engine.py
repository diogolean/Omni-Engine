"""Compatibility shim. The implementation is production.formats.reference.reference_reel_engine."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.formats.reference.reference_reel_engine", run_name="__main__")
else:
    from production.formats.reference import reference_reel_engine as _impl

    sys.modules[__name__] = _impl
