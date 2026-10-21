"""Compatibility shim. The implementation is production.media.video_engine."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.media.video_engine", run_name="__main__")
else:
    from production.media import video_engine as _impl

    sys.modules[__name__] = _impl
