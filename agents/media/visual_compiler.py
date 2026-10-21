"""Compatibility shim. The implementation is production.providers.image.visual_compiler."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.providers.image.visual_compiler", run_name="__main__")
else:
    from production.providers.image import visual_compiler as _impl

    sys.modules[__name__] = _impl
