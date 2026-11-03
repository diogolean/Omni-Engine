"""Compatibility shim. The implementation is production.cost.google_guardrail."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.cost.google_guardrail", run_name="__main__")
else:
    from production.cost import google_guardrail as _impl

    sys.modules[__name__] = _impl
