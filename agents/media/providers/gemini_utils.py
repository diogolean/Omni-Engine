"""Compatibility shim. The implementation is production.providers.llm.gemini_utils."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.providers.llm.gemini_utils", run_name="__main__")
else:
    from production.providers.llm import gemini_utils as _impl

    sys.modules[__name__] = _impl
