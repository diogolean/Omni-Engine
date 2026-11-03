"""Compatibility shim. The implementation is products.aiwake.animator.factory.generate_concepts."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("products.aiwake.animator.factory.generate_concepts", run_name="__main__")
else:
    from products.aiwake.animator.factory import generate_concepts as _impl

    sys.modules[__name__] = _impl
