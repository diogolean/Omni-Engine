"""Compatibility shim. The implementation is animation.parametric.compositor."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("animation.parametric.compositor", run_name="__main__")
else:
    from animation.parametric import compositor as _impl
    from core.animator.aiwake_plate import apply_compositor

    apply_compositor(_impl)
    sys.modules[__name__] = _impl
