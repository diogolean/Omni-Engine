"""Compatibility shim. The implementation is production.qa.reel_visual_qa."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.qa.reel_visual_qa", run_name="__main__")
else:
    from production.qa import reel_visual_qa as _impl

    sys.modules[__name__] = _impl
