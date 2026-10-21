"""Compatibility shim. The implementation is production.qa.timings."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.qa.timings", run_name="__main__")
else:
    from production.qa import timings as _impl

    sys.modules[__name__] = _impl
