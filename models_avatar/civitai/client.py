"""Compatibility shim. The implementation is production.providers.avatar.civitai.client."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.providers.avatar.civitai.client", run_name="__main__")
else:
    from production.providers.avatar.civitai import client as _impl

    sys.modules[__name__] = _impl
