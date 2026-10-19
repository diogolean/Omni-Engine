# -*- coding: utf-8 -*-
"""Channel tests run without the developer's real ``.env``.

Shared with ``tests/conftest.py``; see ``tests/env_isolation.py``.
"""
from tests.env_isolation import isolate_dotenv

isolate_dotenv()
