"""Compatibility shim. The implementation is production.writers.caption_engine."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.writers.caption_engine", run_name="__main__")
else:
    from production.writers import caption_engine as _impl

    sys.modules[__name__] = _impl
