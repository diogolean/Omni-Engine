"""Compatibility shim. The implementation is animation.parametric.factory.puppet_assembler."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("animation.parametric.factory.puppet_assembler", run_name="__main__")
else:
    from animation.parametric.factory import puppet_assembler as _impl
    sys.modules[__name__] = _impl
