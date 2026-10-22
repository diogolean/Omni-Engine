"""Compatibility shim. The implementation is shared.storage.imgbb_client."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("shared.storage.imgbb_client", run_name="__main__")
else:
    from shared.storage import imgbb_client as _impl

    sys.modules[__name__] = _impl
