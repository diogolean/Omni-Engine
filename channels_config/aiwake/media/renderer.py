"""Compatibility shim. The implementation is products.aiwake.media.renderer."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("products.aiwake.media.renderer", run_name="__main__")
else:
    from products.aiwake.media import renderer as _impl

    sys.modules[__name__] = _impl
