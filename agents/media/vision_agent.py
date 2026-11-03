"""Compatibility shim. The implementation is production.providers.vision.vision_agent."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.providers.vision.vision_agent", run_name="__main__")
else:
    from production.providers.vision import vision_agent as _impl

    sys.modules[__name__] = _impl
