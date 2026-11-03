"""Compatibility shim. The implementation is production.pipeline.stages.variant."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.pipeline.stages.variant", run_name="__main__")
else:
    from production.pipeline.stages import variant as _impl

    sys.modules[__name__] = _impl
