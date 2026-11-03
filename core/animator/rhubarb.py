"""Compatibility shim. The implementation is animation.parametric.rhubarb."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("animation.parametric.rhubarb", run_name="__main__")
else:
    from animation.parametric import rhubarb as _impl
    sys.modules[__name__] = _impl
