# -*- coding: utf-8 -*-
"""Small HTTP wrapper. Tests pass their own client with the same ``get`` method."""

from __future__ import annotations

import json
from typing import Protocol

# Wikimedia blocks generic clients. The policy requires a descriptive
# User-Agent that names the tool and a contact URL.
USER_AGENT = (
    "OmniEngine/1.0 (https://github.com/diogolean/Omni-Engine; "
    "free-license image sourcing)"
)


class HttpResponse:
    def __init__(
        self,
        status: int,
        content: bytes,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = int(status)
        self.content = content or b""
        self.headers = {str(key).lower(): str(value) for key, value in (headers or {}).items()}

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def json(self) -> dict | list:
        return json.loads(self.content.decode("utf-8"))


class HttpClient(Protocol):
    def get(
        self,
        url: str,
        *,
        params: dict | None = None,
        headers: dict | None = None,
    ) -> HttpResponse: ...


class RequestsClient:
    def __init__(self, timeout: float = 12.0) -> None:
        self.timeout = timeout

    def get(
        self,
        url: str,
        *,
        params: dict | None = None,
        headers: dict | None = None,
    ) -> HttpResponse:
        import requests

        merged = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if headers:
            merged.update(headers)
        response = requests.get(url, params=params, headers=merged, timeout=self.timeout)
        return HttpResponse(response.status_code, response.content, dict(response.headers))
