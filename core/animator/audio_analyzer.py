"""Compatibility shim. The implementation is animation.parametric.audio_analyzer."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("animation.parametric.audio_analyzer", run_name="__main__")
else:
    from animation.parametric import audio_analyzer as _impl
    sys.modules[__name__] = _impl
