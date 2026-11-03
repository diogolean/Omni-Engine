"""Compatibility shim. The implementation is production.media.reel_sequence_engine."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.media.reel_sequence_engine", run_name="__main__")
else:
    from production.media import reel_sequence_engine as _impl

    sys.modules[__name__] = _impl
