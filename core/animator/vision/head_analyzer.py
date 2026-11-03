"""Compatibility shim. The implementation is products.aiwake.animator.vision.head_analyzer."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("products.aiwake.animator.vision.head_analyzer", run_name="__main__")
else:
    from products.aiwake.animator.vision import head_analyzer as _impl

    sys.modules[__name__] = _impl
