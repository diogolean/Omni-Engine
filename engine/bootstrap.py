"""Compatibility shim. The implementation is production.pipeline.bootstrap."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.pipeline.bootstrap", run_name="__main__")
else:
    from production.pipeline import bootstrap as _impl

    sys.modules[__name__] = _impl
