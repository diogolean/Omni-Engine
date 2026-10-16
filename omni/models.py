"""Shared publishing types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Account:
    """One branding on one platform.

    ``active`` is the publish gate: callers must not hit the network when
    it is false. Secrets are named, not stored, so ``.env`` stays the only
    place tokens live.
    """

    branding: str
    platform: str
    account_id: str
    active: bool = True
    token_env: str = ""
    board_id_env: str = ""
    board_name: str = ""
    board_name_env: str = ""
    refresh_token_env: str = ""
    token_file: str = ""
    extra: dict[str, str] = field(default_factory=dict)


@dataclass
class Content:
    """What a publisher sends.

    ``idempotency_key`` is required. A second ``publish`` with the same key
    returns the stored result and does not call the platform again.
    """

    idempotency_key: str
    title: str = ""
    body: str = ""
    link: str | None = None
    media_path: str | None = None
    media_bytes: bytes | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class PublishResult:
    ok: bool
    platform: str
    idempotency_key: str
    external_id: str | None = None
    skipped: bool = False
    duplicate: bool = False
    error: str | None = None
