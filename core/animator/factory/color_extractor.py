"""Compatibility shim. The implementation is animation.parametric.factory.color_extractor."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("animation.parametric.factory.color_extractor", run_name="__main__")
else:
    from animation.parametric.factory import color_extractor as _impl
    sys.modules[__name__] = _impl
