"""Compatibility shim. The implementation is products.aiwake.tools.retitle_template_uploads."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("products.aiwake.tools.retitle_template_uploads", run_name="__main__")
else:
    from products.aiwake.tools import retitle_template_uploads as _impl

    sys.modules[__name__] = _impl
