"""Compatibility shim. The implementation is production.formats.economic_reel.grounding.still_cache."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.formats.economic_reel.grounding.still_cache", run_name="__main__")
else:
    from production.formats.economic_reel.grounding import still_cache as _impl

    sys.modules[__name__] = _impl
