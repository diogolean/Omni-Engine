"""Compatibility shim. The implementation is production.pipeline.stages.state."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.pipeline.stages.state", run_name="__main__")
else:
    from production.pipeline.stages import state as _impl

    sys.modules[__name__] = _impl
