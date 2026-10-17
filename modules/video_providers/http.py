# -*- coding: utf-8 -*-
"""Stdlib HTTP with the factory's browser User-Agent.

RunPod's proxy rejects the default Python user agent, so ``models_avatar``
sends a desktop Chrome string on every request. Video calls do the same.
The ElevenLabs SDK (2.73.0) sends ``User-Agent: elevenlabs/2.73.0`` instead,
which is why this layer speaks HTTP directly.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from models_avatar.http_client import BROWSER_USER_AGENT

from modules.video_providers.errors import VideoProviderError

Transport = Callable[..., Any]

# Statuses that are worth another poll. A create is not retried automatically:
# a timeout after the server accepted the job would start a second one.
RETRYABLE_HTTP_STATUSES = frozenset({408, 429, 500, 502, 503, 504})


@dataclass
class HttpResult:
    status: int
    body: bytes
    headers: dict[str, str]
    url: str

    def json(self) -> Any:
        if not self.body:
            return None
        return json.loads(self.body.decode("utf-8"))

    def header(self, name: str) -> str:
        return self.headers.get(name.lower(), "")


def _default_transport(request: Request, timeout: float) -> Any:
    return urlopen(request, timeout=timeout)


def _build_request(
    method: str,
    url: str,
    *,
    headers: Mapping[str, str] | None,
    body: bytes | None,
) -> Request:
    hdrs: dict[str, str] = {
        "User-Agent": BROWSER_USER_AGENT,
        "Accept": "*/*",
    }
    if headers:
        hdrs.update({str(k): str(v) for k, v in headers.items()})
    # A caller must not put Python-urllib or the SDK token back on the wire.
    hdrs["User-Agent"] = BROWSER_USER_AGENT
    return Request(url, data=body, headers=hdrs, method=method.upper())


def _header_map(raw: Any) -> dict[str, str]:
    if raw is None:
        return {}
    try:
        pairs = dict(raw).items()
    except Exception:  # noqa: BLE001
        pairs = []
    return {str(k).lower(): str(v) for k, v in pairs}


def exchange(
    method: str,
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    body: bytes | None = None,
    timeout: float = 120.0,
    transport: Transport | None = None,
) -> HttpResult:
    """Perform one HTTP call and return the body, including error statuses.

    ``transport`` replaces ``urlopen`` in tests. It is called as
    ``transport(urllib.request.Request, timeout)``.
    """
    req = _build_request(method, url, headers=headers, body=body)
    opener = transport or _default_transport
    try:
        opened = opener(req, timeout=timeout)
    except HTTPError as exc:
        detail = b""
        try:
            detail = exc.read()
        except Exception:  # noqa: BLE001
            detail = str(exc).encode("utf-8", errors="replace")
        headers_out = _header_map(getattr(exc, "headers", None))
        return HttpResult(status=int(exc.code), body=detail, headers=headers_out, url=url)
    except URLError as exc:
        raise VideoProviderError(
            f"Connection failed {method.upper()} {url}: {exc.reason}",
            retryable=True,
        ) from exc

    if hasattr(opened, "__enter__"):
        with opened as resp:
            return _read_all(resp, url)
    return _read_all(opened, url)


def _read_all(resp: Any, url: str) -> HttpResult:
    status = int(getattr(resp, "status", None) or getattr(resp, "code", 200))
    body = resp.read()
    headers = _header_map(getattr(resp, "headers", None))
    return HttpResult(status=status, body=body, headers=headers, url=url)


def read_chunks(resp: Any, chunk: int = 1024 * 1024) -> Any:
    """Yield body chunks from a response that may be a urllib stream."""
    while True:
        block = resp.read(chunk)
        if not block:
            return
        yield block


def open_stream(
    method: str,
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    timeout: float = 300.0,
    transport: Transport | None = None,
) -> tuple[int, dict[str, str], Any]:
    """Open a response for streaming. Caller closes it.

    Returns ``(status, headers, response)``. HTTP error statuses are returned
    rather than raised, so the caller can drop a partial file.
    """
    req = _build_request(method, url, headers=headers, body=None)
    opener = transport or _default_transport
    try:
        opened = opener(req, timeout=timeout)
    except HTTPError as exc:
        return int(exc.code), _header_map(getattr(exc, "headers", None)), exc
    except URLError as exc:
        raise VideoProviderError(
            f"Connection failed {method.upper()} {url}: {exc.reason}",
            retryable=True,
        ) from exc
    if hasattr(opened, "__enter__") and not hasattr(opened, "read"):
        opened = opened.__enter__()
    status = int(getattr(opened, "status", None) or getattr(opened, "code", 200))
    return status, _header_map(getattr(opened, "headers", None)), opened


def http_status_retryable(status: int) -> bool:
    return int(status) in RETRYABLE_HTTP_STATUSES


def dumps_json(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
