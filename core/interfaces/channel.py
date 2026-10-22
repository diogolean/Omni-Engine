"""Compatibility shim. The implementation is production.pipeline.interfaces.channel."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.pipeline.interfaces.channel", run_name="__main__")
else:
    from production.pipeline.interfaces import channel as _impl

    sys.modules[__name__] = _impl
