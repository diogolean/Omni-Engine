"""Compatibility shim. The browser publisher module lives in distribution.publishers.browser.facebook_scheduler.logger."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("distribution.publishers.browser.facebook_scheduler.logger", run_name="__main__")
else:
    from distribution.publishers.browser.facebook_scheduler import logger as _impl

    sys.modules[__name__] = _impl
