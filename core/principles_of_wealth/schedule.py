"""Compatibility shim. The implementation is production.formats.wealth.schedule."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.formats.wealth.schedule", run_name="__main__")
else:
    from production.formats.wealth import schedule as _impl

    sys.modules[__name__] = _impl
