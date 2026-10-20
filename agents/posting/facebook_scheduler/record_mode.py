"""Compatibility shim. The browser publisher module lives in distribution.publishers.browser.facebook_scheduler.record_mode."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("distribution.publishers.browser.facebook_scheduler.record_mode", run_name="__main__")
else:
    from distribution.publishers.browser.facebook_scheduler import record_mode as _impl

    sys.modules[__name__] = _impl
