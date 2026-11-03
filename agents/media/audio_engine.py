"""Compatibility shim. The implementation is production.providers.tts.audio_engine."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.providers.tts.audio_engine", run_name="__main__")
else:
    from production.providers.tts import audio_engine as _impl

    sys.modules[__name__] = _impl
