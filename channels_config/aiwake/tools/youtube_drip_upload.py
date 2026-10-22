"""Compatibility shim. The implementation is products.aiwake.tools.youtube_drip_upload."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("products.aiwake.tools.youtube_drip_upload", run_name="__main__")
else:
    from products.aiwake.tools import youtube_drip_upload as _impl

    sys.modules[__name__] = _impl
