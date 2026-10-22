"""Compatibility shim. The implementation is production.pipeline.agentic.llm."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.pipeline.agentic.llm", run_name="__main__")
else:
    from production.pipeline.agentic import llm as _impl

    sys.modules[__name__] = _impl
