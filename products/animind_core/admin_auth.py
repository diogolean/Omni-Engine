"""Server-to-server admin proof.

The browser role header is ignored. The web server sends
``X-Animind-Admin`` only after it has already decided the account is an
admin or super-admin. The token lives in ``ANIMIND_ADMIN_TOKEN``.
"""
from __future__ import annotations

import hmac
import os

HEADER = "x-animind-admin"
ENV = "ANIMIND_ADMIN_TOKEN"
MIN_TOKEN = 16


def trusted(header: str | None) -> bool:
    """True only when the header matches the server token."""
    expected = (os.getenv(ENV) or "").strip()
    got = (header or "").strip()
    if len(expected) < MIN_TOKEN or len(got) != len(expected):
        return False
    return hmac.compare_digest(got, expected)
