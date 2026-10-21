"""Compatibility shim. The implementation is production.cost.gemini_usage."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.cost.gemini_usage", run_name="__main__")
else:
    from production.cost import gemini_usage as _impl

    sys.modules[__name__] = _impl
