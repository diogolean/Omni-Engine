"""Compatibility shim. The implementation is production.formats.wealth.seo_catalog."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.formats.wealth.seo_catalog", run_name="__main__")
else:
    from production.formats.wealth import seo_catalog as _impl

    sys.modules[__name__] = _impl
