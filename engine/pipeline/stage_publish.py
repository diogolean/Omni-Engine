"""Compatibility shim. The implementation is production.pipeline.stages.stage_publish."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.pipeline.stages.stage_publish", run_name="__main__")
else:
    from production.pipeline.stages import stage_publish as _impl

    sys.modules[__name__] = _impl
