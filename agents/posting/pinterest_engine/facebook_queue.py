"""Compatibility shim. The Facebook queue lives in distribution.scheduling.queue."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("distribution.scheduling.queue", run_name="__main__")
else:
    from distribution.scheduling import queue as _impl

    sys.modules[__name__] = _impl
