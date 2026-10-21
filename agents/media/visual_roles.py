"""Compatibility shim. The implementation is production.media.visual_roles."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.media.visual_roles", run_name="__main__")
else:
    from production.media import visual_roles as _impl

    sys.modules[__name__] = _impl
