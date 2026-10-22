"""Compatibility shim. The implementation is animation.parametric.factory.layer_slicer."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("animation.parametric.factory.layer_slicer", run_name="__main__")
else:
    from animation.parametric.factory import layer_slicer as _impl
    sys.modules[__name__] = _impl
