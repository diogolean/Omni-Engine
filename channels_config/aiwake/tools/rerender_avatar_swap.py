"""Compatibility shim. The implementation is products.aiwake.tools.rerender_avatar_swap."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("products.aiwake.tools.rerender_avatar_swap", run_name="__main__")
else:
    from products.aiwake.tools import rerender_avatar_swap as _impl

    sys.modules[__name__] = _impl
