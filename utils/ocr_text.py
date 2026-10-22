"""Compatibility shim. The implementation is production.formats.quotes.ocr_text."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.formats.quotes.ocr_text", run_name="__main__")
else:
    from production.formats.quotes import ocr_text as _impl

    sys.modules[__name__] = _impl
