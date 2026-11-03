"""Compatibility shim. The implementation is production.providers.llm.model_router."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.providers.llm.model_router", run_name="__main__")
else:
    from production.providers.llm import model_router as _impl

    sys.modules[__name__] = _impl
