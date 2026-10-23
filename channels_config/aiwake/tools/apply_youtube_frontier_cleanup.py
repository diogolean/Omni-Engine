"""Compatibility shim. The implementation is products.aiwake.tools.apply_youtube_frontier_cleanup."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("products.aiwake.tools.apply_youtube_frontier_cleanup", run_name="__main__")
else:
    from products.aiwake.tools import apply_youtube_frontier_cleanup as _impl

    sys.modules[__name__] = _impl
