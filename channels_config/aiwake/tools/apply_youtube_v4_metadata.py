"""Compatibility shim. The implementation is products.aiwake.tools.apply_youtube_v4_metadata."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("products.aiwake.tools.apply_youtube_v4_metadata", run_name="__main__")
else:
    from products.aiwake.tools import apply_youtube_v4_metadata as _impl

    sys.modules[__name__] = _impl
