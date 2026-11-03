"""Compatibility shim. The implementation is production.pipeline.voice."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.pipeline.voice", run_name="__main__")
else:
    from production.pipeline import voice as _impl

    sys.modules[__name__] = _impl
