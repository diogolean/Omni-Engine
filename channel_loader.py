"""Compatibility shim. The implementation is production.pipeline.channel_context."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.pipeline.channel_context", run_name="__main__")
else:
    from production.pipeline import channel_context as _impl

    sys.modules[__name__] = _impl
