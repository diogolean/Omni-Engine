"""Compatibility shim. The implementation is production.providers.avatar.service."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.providers.avatar.service", run_name="__main__")
else:
    from production.providers.avatar import service as _impl

    sys.modules[__name__] = _impl
