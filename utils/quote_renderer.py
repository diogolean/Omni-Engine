"""Compatibility shim. The implementation is production.formats.quotes.quote_renderer."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.formats.quotes.quote_renderer", run_name="__main__")
else:
    from production.formats.quotes import quote_renderer as _impl

    sys.modules[__name__] = _impl
