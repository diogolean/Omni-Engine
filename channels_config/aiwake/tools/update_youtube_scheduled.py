"""Compatibility shim. The implementation is products.aiwake.tools.update_youtube_scheduled."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("products.aiwake.tools.update_youtube_scheduled", run_name="__main__")
else:
    from products.aiwake.tools import update_youtube_scheduled as _impl

    sys.modules[__name__] = _impl
