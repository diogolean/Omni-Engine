# -*- coding: utf-8 -*-
"""Stdlib HTTP with a browser User-Agent.

RunPod's Cloudflare proxy in front of ``*.proxy.runpod.net`` rejects the
default ``Python-urllib`` user agent. Every request this package sends sets
a browser User-Agent, including ``/prompt``, ``/history``, and ``/view``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from models_avatar.errors import HttpStatusError, RetryableCivitaiError, RetryableGenerationError

# A current desktop Chrome UA. Do not shorten this to a library token.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

Transport = Callable[..., Any]


@dataclass
class HttpResponse:
    status: int
    body: bytes
    headers: dict[str, str] = field(default_factory=dict)
    url: str = ""

    def json(self) -> Any:
        if not self.body:
            return None
        return json.loads(self.body.decode("utf-8"))

    def header(self, name: str) -> str:
        return self.headers.get(name.lower(), "")


def join_url(base: str, path: str) -> str:
    root = base.split("#", 1)[0].strip().rstrip("/") + "/"
    return urljoin(root, path.lstrip("/"))


def _default_transport(request: Request, timeout: float) -> Any:
    return urlopen(request, timeout=timeout)


def _materialize(resp: Any, url: str) -> HttpResponse:
    status = int(getattr(resp, "status", None) or getattr(resp, "code", 200))
    body = resp.read()
    raw_headers = getattr(resp, "headers", {}) or {}
    headers = {str(k).lower(): str(v) for k, v in dict(raw_headers).items()}
    return HttpResponse(status=status, body=body, headers=headers, url=url)


def request(
    method: str,
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    body: bytes | None = None,
    timeout: float = 120.0,
    transport: Transport | None = None,
    error_cls: type[Exception] = RetryableGenerationError,
) -> HttpResponse:
    """Perform one HTTP request.

    ``transport`` replaces ``urlopen`` in tests. It is called as
    ``transport(urllib.request.Request, timeout)`` and may return a response
    or a context manager yielding one.
    """
    hdrs: dict[str, str] = {
        "User-Agent": BROWSER_USER_AGENT,
        "Accept": "*/*",
    }
    if headers:
        hdrs.update(dict(headers))
    # Callers cannot put the blocked urllib UA back on the wire.
    agent = hdrs.get("User-Agent") or ""
    if not agent or "python-urllib" in agent.lower() or agent.lower().startswith("python/"):
        hdrs["User-Agent"] = BROWSER_USER_AGENT

    req = Request(url, data=body, headers=hdrs, method=method.upper())
    opener = transport or _default_transport
    try:
        opened = opener(req, timeout=timeout)
    except HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            detail = str(exc)
        raise HttpStatusError(int(exc.code), method.upper(), url, detail) from exc
    except URLError as exc:
        raise error_cls(f"Connection failed {method.upper()} {url}: {exc}") from exc

    if hasattr(opened, "__enter__"):
        with opened as resp:
            result = _materialize(resp, url)
    else:
        result = _materialize(opened, url)
    if result.status >= 400:
        detail = result.body.decode("utf-8", errors="replace")
        raise HttpStatusError(result.status, method.upper(), url, detail)
    return result


def request_json(
    method: str,
    url: str,
    *,
    payload: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float = 120.0,
    transport: Transport | None = None,
    error_cls: type[Exception] = RetryableGenerationError,
) -> Any:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    hdrs = dict(headers or {})
    if body is not None:
        hdrs.setdefault("Content-Type", "application/json")
    response = request(
        method,
        url,
        headers=hdrs,
        body=body,
        timeout=timeout,
        transport=transport,
        error_cls=error_cls,
    )
    if not response.body:
        return None
    try:
        return response.json()
    except json.JSONDecodeError as exc:
        raise error_cls(f"Non-JSON response from {url}: {response.body[:300]!r}") from exc


def civitai_request_json(
    method: str,
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    timeout: float = 120.0,
    transport: Transport | None = None,
) -> Any:
    return request_json(
        method,
        url,
        headers=headers,
        timeout=timeout,
        transport=transport,
        error_cls=RetryableCivitaiError,
    )
