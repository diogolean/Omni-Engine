"""Compatibility shim. The implementation is products.aiwake.tools.seo_caption."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("products.aiwake.tools.seo_caption", run_name="__main__")
else:
    from products.aiwake.tools import seo_caption as _impl

    sys.modules[__name__] = _impl
