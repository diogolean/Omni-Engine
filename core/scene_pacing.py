"""Compatibility shim. The implementation is production.media.scene_pacing."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.media.scene_pacing", run_name="__main__")
else:
    from production.media import scene_pacing as _impl

    sys.modules[__name__] = _impl
