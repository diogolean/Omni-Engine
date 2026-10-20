"""Compatibility shim. The browser publisher module lives in distribution.publishers.browser.facebook_scheduler.media_scheduler_base."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("distribution.publishers.browser.facebook_scheduler.media_scheduler_base", run_name="__main__")
else:
    from distribution.publishers.browser.facebook_scheduler import media_scheduler_base as _impl

    sys.modules[__name__] = _impl
