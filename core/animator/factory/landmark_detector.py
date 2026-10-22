"""Compatibility shim. The implementation is animation.parametric.factory.landmark_detector."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("animation.parametric.factory.landmark_detector", run_name="__main__")
else:
    from animation.parametric.factory import landmark_detector as _impl
    sys.modules[__name__] = _impl
