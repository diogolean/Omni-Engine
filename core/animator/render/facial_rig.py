"""Compatibility shim. The implementation is products.aiwake.animator.render.facial_rig."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("products.aiwake.animator.render.facial_rig", run_name="__main__")
else:
    from products.aiwake.animator.render import facial_rig as _impl

    sys.modules[__name__] = _impl
