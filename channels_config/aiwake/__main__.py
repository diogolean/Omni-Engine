"""Compatibility shim. The implementation is products.aiwake."""
from __future__ import annotations

if __name__ == "__main__":
    import runpy

    runpy.run_module("products.aiwake", run_name="__main__")
