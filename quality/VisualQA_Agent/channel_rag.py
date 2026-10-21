"""Compatibility shim. The implementation is production.qa.judges.visual_critic.channel_rag."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.qa.judges.visual_critic.channel_rag", run_name="__main__")
else:
    from production.qa.judges.visual_critic import channel_rag as _impl

    sys.modules[__name__] = _impl
