"""Compatibility shim. The implementation is products.aiwake.tools.repair_caption_completeness."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("products.aiwake.tools.repair_caption_completeness", run_name="__main__")
else:
    from products.aiwake.tools import repair_caption_completeness as _impl

    sys.modules[__name__] = _impl
