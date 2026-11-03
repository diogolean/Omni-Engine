"""Compatibility shim. The implementation is production.providers.avatar.paths."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.providers.avatar.paths", run_name="__main__")
else:
    from production.providers.avatar import paths as _impl

    sys.modules[__name__] = _impl
