"""Compatibility shim. The implementation is production.pipeline.stills."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.pipeline.stills", run_name="__main__")
else:
    from production.pipeline import stills as _impl

    sys.modules[__name__] = _impl
