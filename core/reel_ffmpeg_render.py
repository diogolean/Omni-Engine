"""Compatibility shim. The implementation is production.media.reel_ffmpeg_render."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.media.reel_ffmpeg_render", run_name="__main__")
else:
    from production.media import reel_ffmpeg_render as _impl

    sys.modules[__name__] = _impl
