"""Compatibility shim. The implementation is production.providers.llm.model_api_flows."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.providers.llm.model_api_flows", run_name="__main__")
else:
    from production.providers.llm import model_api_flows as _impl

    sys.modules[__name__] = _impl
