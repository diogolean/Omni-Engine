"""Compatibility shim. The implementation is production.formats.lofi.caption_style_lofi."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.formats.lofi.caption_style_lofi", run_name="__main__")
else:
    from production.formats.lofi import caption_style_lofi as _impl

    sys.modules[__name__] = _impl
