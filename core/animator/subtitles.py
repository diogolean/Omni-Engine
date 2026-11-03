"""Compatibility shim. The implementation is animation.parametric.subtitles."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("animation.parametric.subtitles", run_name="__main__")
else:
    from animation.parametric import subtitles as _impl
    sys.modules[__name__] = _impl
