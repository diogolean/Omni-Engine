"""Compatibility shim. The implementation is products.aiwake.animator.render.visemes."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("products.aiwake.animator.render.visemes", run_name="__main__")
else:
    from products.aiwake.animator.render import visemes as _impl

    sys.modules[__name__] = _impl
