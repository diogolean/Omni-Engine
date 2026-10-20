# -*- coding: utf-8 -*-
"""Environment for the shared memory client. Any host, not only localhost."""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit

from shared.env import env_bool
from shared.paths import factory_root

DEFAULT_BASE_URL = "http://localhost:6767"
HEALTH_TIMEOUT_S = 1.5
REQUEST_TIMEOUT_S = 8.0
HEALTH_CACHE_S = 60.0
SIMILARITY_THRESHOLD = 0.75


def env_flag(name: str) -> bool:
    """True for ``1/true/yes/on``. Same rule as :func:`shared.env.env_bool`."""
    return env_bool(name)


def host_is_local(base_url: str) -> bool:
    host = (urlsplit(base_url).hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"}


def _key_file() -> str:
    path = factory_root() / ".supermemory" / "api-key"
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


@dataclass(frozen=True)
class MemorySettings:
    base_url: str
    api_key: str
    disabled: bool
    health_timeout_s: float = HEALTH_TIMEOUT_S
    request_timeout_s: float = REQUEST_TIMEOUT_S
    health_cache_s: float = HEALTH_CACHE_S
    similarity_threshold: float = SIMILARITY_THRESHOLD

    @property
    def local_server(self) -> bool:
        return host_is_local(self.base_url)


def load_settings(
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    disabled: bool | None = None,
    health_timeout_s: float | None = None,
    similarity_threshold: float | None = None,
) -> MemorySettings:
    """Read URL and key from the environment for whichever host is configured.

    An explicit argument wins. When the key argument is omitted, the value is
    ``SUPERMEMORY_API_KEY`` or the git-ignored ``.supermemory/api-key`` file.
    The file is not limited to localhost.
    """
    if base_url is None:
        base_url = (os.getenv("SUPERMEMORY_BASE_URL") or DEFAULT_BASE_URL).strip()
    base_url = base_url.rstrip("/") or DEFAULT_BASE_URL
    if api_key is None:
        api_key = (os.getenv("SUPERMEMORY_API_KEY") or "").strip() or _key_file()
    else:
        api_key = api_key.strip()
    if disabled is None:
        disabled = env_flag("SUPERMEMORY_DISABLED")
    return MemorySettings(
        base_url=base_url,
        api_key=api_key,
        disabled=bool(disabled),
        health_timeout_s=HEALTH_TIMEOUT_S if health_timeout_s is None else float(health_timeout_s),
        similarity_threshold=(
            SIMILARITY_THRESHOLD if similarity_threshold is None else float(similarity_threshold)
        ),
    )
