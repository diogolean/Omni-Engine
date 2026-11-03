"""Compatibility shim. The implementation is production.pipeline.post_type_registry."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.pipeline.post_type_registry", run_name="__main__")
else:
    from production.pipeline import post_type_registry as _impl

    sys.modules[__name__] = _impl
