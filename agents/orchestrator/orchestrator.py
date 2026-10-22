"""Compatibility shim. The implementation is production.pipeline.agentic.orchestrator."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.pipeline.agentic.orchestrator", run_name="__main__")
else:
    from production.pipeline.agentic import orchestrator as _impl

    sys.modules[__name__] = _impl
