"""Temporary shim: the worker moved to ``core.worker``. Removed in PR-O4.

``python -m omni.worker`` (Dockerfile, docker-compose) keeps working until
PR-O3 switches the entrypoint to ``python -m core.worker``.
"""
import sys as _sys
from pathlib import Path as _Path

_ROOT = _Path(__file__).resolve().parents[1]
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))

import core.worker as _mod  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(_mod.main())

_sys.modules[__name__] = _mod
