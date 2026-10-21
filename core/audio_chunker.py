"""Compatibility shim. The implementation is production.media.audio_chunker."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.media.audio_chunker", run_name="__main__")
else:
    from production.media import audio_chunker as _impl

    sys.modules[__name__] = _impl
