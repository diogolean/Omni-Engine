"""Compatibility shim. The implementation is production.formats.mei.visual_architect."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.formats.mei.visual_architect", run_name="__main__")
else:
    from production.formats.mei import visual_architect as _impl

    sys.modules[__name__] = _impl
