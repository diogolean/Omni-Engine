"""Compatibility shim. The implementation is production.pipeline.cli_parser."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.pipeline.cli_parser", run_name="__main__")
else:
    from production.pipeline import cli_parser as _impl

    sys.modules[__name__] = _impl
