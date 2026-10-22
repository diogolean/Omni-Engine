"""Compatibility shim. The implementation is production.formats.lofi.setting_archetypes."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.formats.lofi.setting_archetypes", run_name="__main__")
else:
    from production.formats.lofi import setting_archetypes as _impl

    sys.modules[__name__] = _impl
