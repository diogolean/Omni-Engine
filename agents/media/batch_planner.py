"""Compatibility shim. The implementation is production.media.batch_planner."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.media.batch_planner", run_name="__main__")
else:
    from production.media import batch_planner as _impl

    sys.modules[__name__] = _impl
