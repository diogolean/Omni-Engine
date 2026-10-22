"""Compatibility shim. The implementation is products.aiwake.tools.upload_youtube_v4_queue."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("products.aiwake.tools.upload_youtube_v4_queue", run_name="__main__")
else:
    from products.aiwake.tools import upload_youtube_v4_queue as _impl

    sys.modules[__name__] = _impl
