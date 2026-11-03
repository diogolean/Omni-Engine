"""Compatibility shim. The implementation is production.pipeline.agentic.starters."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.pipeline.agentic.starters", run_name="__main__")
else:
    from production.pipeline.agentic import starters as _impl

    sys.modules[__name__] = _impl
