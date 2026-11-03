"""Compatibility shim. The implementation is shared.storage.drive_source."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("shared.storage.drive_source", run_name="__main__")
else:
    from shared.storage import drive_source as _impl

    sys.modules[__name__] = _impl
