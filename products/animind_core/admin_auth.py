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
SUPER_ENV = "ANIMIND_SUPER_ADMIN_TOKEN"
MIN_TOKEN = 16


def _matches(header: str | None, env_name: str) -> bool:
    expected = (os.getenv(env_name) or "").strip()
    got = (header or "").strip()
    if len(expected) < MIN_TOKEN or len(got) != len(expected):
        return False
    return hmac.compare_digest(got, expected)


def trusted_super(header: str | None) -> bool:
    """Only the separate server-side super-admin credential."""
    return _matches(header, SUPER_ENV)


def trusted(header: str | None) -> bool:
    """Admin and super-admin may generate; browser role headers never count."""
    return _matches(header, ENV) or trusted_super(header)
