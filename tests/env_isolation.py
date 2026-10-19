# -*- coding: utf-8 -*-
"""Keep the developer's real ``.env`` out of the test run.

CI has no ``.env``, so tests are written against a bare environment. On a
dev PC, ``config.py`` and several tools call ``load_dotenv`` at import time
and pulled live tokens into tests (wrong expectations, and a live Graph call
from the Momma Circle token test). ``isolate_dotenv`` runs from the initial
conftests, before any test module is imported:

* ``OMNI_SKIP_DOTENV=1`` makes ``config._load_project_dotenv`` a no-op;
* ``dotenv.load_dotenv`` becomes a no-op for everything else.

Variables already exported in the shell are left alone.
"""
from __future__ import annotations

import os


def isolate_dotenv() -> None:
    os.environ["OMNI_SKIP_DOTENV"] = "1"
    try:
        import dotenv
        import dotenv.main
    except ImportError:
        return

    def _no_dotenv(*_args, **_kwargs) -> bool:
        return False

    dotenv.load_dotenv = _no_dotenv
    dotenv.main.load_dotenv = _no_dotenv
