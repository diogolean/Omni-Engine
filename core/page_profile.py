"""Compatibility shim. The implementation is production.pipeline.page_profile."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.pipeline.page_profile", run_name="__main__")
else:
    from production.pipeline import page_profile as _impl

    sys.modules[__name__] = _impl
