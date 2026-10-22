"""Compatibility shim. The implementation is production.pipeline.geo_anchors."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.pipeline.geo_anchors", run_name="__main__")
else:
    from production.pipeline import geo_anchors as _impl

    sys.modules[__name__] = _impl
