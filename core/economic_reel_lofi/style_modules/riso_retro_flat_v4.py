"""Compatibility shim. The implementation is production.formats.lofi.style_modules.riso_retro_flat_v4."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.formats.lofi.style_modules.riso_retro_flat_v4", run_name="__main__")
else:
    from production.formats.lofi.style_modules import riso_retro_flat_v4 as _impl

    sys.modules[__name__] = _impl
