"""Compatibility shim. The implementation is production.qa.judges.visual_critic.agent_loop."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.qa.judges.visual_critic.agent_loop", run_name="__main__")
else:
    from production.qa.judges.visual_critic import agent_loop as _impl

    sys.modules[__name__] = _impl
