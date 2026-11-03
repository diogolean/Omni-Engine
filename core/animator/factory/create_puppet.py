"""Compatibility shim. The implementation is products.aiwake.animator.factory.create_puppet."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("products.aiwake.animator.factory.create_puppet", run_name="__main__")
else:
    from products.aiwake.animator.factory import create_puppet as _impl

    sys.modules[__name__] = _impl
