"""Compatibility shim. The implementation is production.providers.avatar.backends.runpod_pod."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.providers.avatar.backends.runpod_pod", run_name="__main__")
else:
    from production.providers.avatar.backends import runpod_pod as _impl

    sys.modules[__name__] = _impl
