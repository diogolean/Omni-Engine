"""Compatibility shim. The implementation is production.pipeline.agentic.criteria."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.pipeline.agentic.criteria", run_name="__main__")
else:
    from production.pipeline.agentic import criteria as _impl

    sys.modules[__name__] = _impl
