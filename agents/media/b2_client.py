"""Compatibility shim. The implementation is shared.storage.b2_client."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("shared.storage.b2_client", run_name="__main__")
else:
    from shared.storage import b2_client as _impl

    sys.modules[__name__] = _impl
