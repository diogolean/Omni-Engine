"""Compatibility shim. The implementation is products.aiwake.media.audio."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("products.aiwake.media.audio", run_name="__main__")
else:
    from products.aiwake.media import audio as _impl

    sys.modules[__name__] = _impl
