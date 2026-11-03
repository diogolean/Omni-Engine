"""Compatibility shim. The implementation is animation.parametric.factory.rigger."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("animation.parametric.factory.rigger", run_name="__main__")
else:
    from animation.parametric.factory import rigger as _impl
    from core.animator.aiwake_plate import apply_rigger

    apply_rigger(_impl)
    sys.modules[__name__] = _impl
