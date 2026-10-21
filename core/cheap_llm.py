"""Compatibility shim. The implementation is production.providers.llm.cheap_llm."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.providers.llm.cheap_llm", run_name="__main__")
else:
    from production.providers.llm import cheap_llm as _impl

    sys.modules[__name__] = _impl
