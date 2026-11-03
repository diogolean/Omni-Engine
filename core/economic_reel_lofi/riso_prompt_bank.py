"""Compatibility shim. The implementation is production.formats.lofi.riso_prompt_bank."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.formats.lofi.riso_prompt_bank", run_name="__main__")
else:
    from production.formats.lofi import riso_prompt_bank as _impl

    sys.modules[__name__] = _impl
