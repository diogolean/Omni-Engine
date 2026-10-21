"""Compatibility shim. The implementation is production.media.subject_brain."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.media.subject_brain", run_name="__main__")
else:
    from production.media import subject_brain as _impl

    sys.modules[__name__] = _impl
