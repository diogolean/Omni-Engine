"""Compatibility shim. The implementation is animation.parametric.puppet."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("animation.parametric.puppet", run_name="__main__")
else:
    from animation.parametric import puppet as _impl
    from core.animator.aiwake_plate import apply_puppet

    apply_puppet(_impl)
    sys.modules[__name__] = _impl
