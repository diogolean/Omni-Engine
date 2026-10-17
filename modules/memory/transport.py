# -*- coding: utf-8 -*-
"""v3 transports. The SDK is preferred; raw HTTP covers a missing install."""

from __future__ import annotations

import logging
from typing import Any, Protocol

from modules.memory.config import MemorySettings

_LOG = logging.getLogger("omni.memory")


class MemoryTransport(Protocol):
    def health(self, timeout_s: float) -> bool: ...

    def search(
        self,
        *,
        query: str,
        container_tag: str,
        search_mode: str,
        limit: int,
    ) -> list[Any]: ...

    def add(
        self,
        *,
        content: str,
        container_tag: str,
        metadata: dict[str, Any] | None,
        custom_id: str,
    ) -> bool: ...

    def list_documents(self, *, container_tag: str, limit: int = 200) -> list[Any]: ...


def _headers(api_key: str) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _status_ok(body: Any) -> bool:
    if not isinstance(body, dict):
        return True
    status = str(body.get("status") or "").strip().lower()
    return status in {"", "ok", "healthy", "ready"}


def _dump(value: Any) -> Any:
    if isinstance(value, dict):
        return value
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            return dump()
        except Exception:  # noqa: BLE001
            return value
    return value


def _rows(payload: Any) -> list[Any]:
    payload = _dump(payload)
    if payload is None:
        return []
    if isinstance(payload, dict):
        rows = payload.get("results") or payload.get("memories") or payload.get("documents") or []
        return list(rows) if isinstance(rows, list) else []
    rows = getattr(payload, "results", None)
    if rows is None:
        rows = getattr(payload, "memories", None)
    return list(rows or [])


def _metadata(value: dict[str, Any] | None) -> dict[str, Any]:
    """SDK metadata values are strings, numbers, booleans, or lists of strings."""
    clean: dict[str, Any] = {}
    for key, raw in (value or {}).items():
        name = str(key)
        if isinstance(raw, (str, int, float, bool)) or raw is None:
            clean[name] = raw if raw is not None else ""
        elif isinstance(raw, (list, tuple)) and all(isinstance(item, str) for item in raw):
            clean[name] = list(raw)
        else:
            clean[name] = str(raw)
    return clean


class HttpTransport:
    """v3 HTTP used when the SDK is absent, and for the health check."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout_s: float,
        session: Any = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_s = timeout_s
        self._session = session

    def _http(self) -> Any:
        if self._session is not None:
            return self._session
        import requests

        return requests

    def health(self, timeout_s: float) -> bool:
        try:
            response = self._http().get(
                f"{self.base_url}/v3/health",
                headers=_headers(self.api_key),
                timeout=timeout_s,
            )
        except Exception:  # noqa: BLE001 — offline is a normal state
            return False
        if getattr(response, "status_code", 599) >= 400:
            return False
        try:
            body = response.json() if getattr(response, "content", b"") else {}
        except Exception:  # noqa: BLE001
            body = {}
        return _status_ok(body)

    def search(
        self,
        *,
        query: str,
        container_tag: str,
        search_mode: str,
        limit: int,
    ) -> list[Any]:
        response = self._http().post(
            f"{self.base_url}/v3/search",
            headers=_headers(self.api_key),
            json={
                "q": query,
                "containerTag": container_tag,
                "searchMode": search_mode,
                "limit": limit,
            },
            timeout=self.timeout_s,
        )
        if getattr(response, "status_code", 599) >= 400:
            raise RuntimeError(f"search status {response.status_code}")
        return _rows(response.json())

    def add(
        self,
        *,
        content: str,
        container_tag: str,
        metadata: dict[str, Any] | None,
        custom_id: str,
    ) -> bool:
        body: dict[str, Any] = {
            "content": content,
            "containerTag": container_tag,
            "metadata": _metadata(metadata),
        }
        if custom_id:
            body["customId"] = custom_id
        response = self._http().post(
            f"{self.base_url}/v3/documents",
            headers=_headers(self.api_key),
            json=body,
            timeout=self.timeout_s,
        )
        status = int(getattr(response, "status_code", 599))
        if status == 409:
            return True
        if status >= 400:
            return False
        return True

    def list_documents(self, *, container_tag: str, limit: int = 200) -> list[Any]:
        found: list[Any] = []
        page = 1
        while page <= 20:
            response = self._http().post(
                f"{self.base_url}/v3/documents/list",
                headers=_headers(self.api_key),
                json={"containerTags": [container_tag], "limit": limit, "page": page},
                timeout=self.timeout_s,
            )
            if getattr(response, "status_code", 599) >= 400:
                break
            payload = response.json()
            found.extend(_rows(payload))
            pagination = payload.get("pagination") if isinstance(payload, dict) else None
            total_pages = 1
            if isinstance(pagination, dict):
                total_pages = int(
                    pagination.get("totalPages") or pagination.get("total_pages") or 1
                )
            if page >= total_pages or not _rows(payload):
                break
            page += 1
        return found


class SdkTransport:
    """``supermemory`` 3.x client. Health stays on HTTP because the SDK has no probe."""

    def __init__(
        self,
        settings: MemorySettings,
        *,
        http: HttpTransport,
        client: Any = None,
    ) -> None:
        self.settings = settings
        self.http = http
        self._client = client

    def client(self) -> Any:
        if self._client is None:
            self._client = build_sdk_client(self.settings)
        return self._client

    def health(self, timeout_s: float) -> bool:
        return self.http.health(timeout_s)

    def search(
        self,
        *,
        query: str,
        container_tag: str,
        search_mode: str,
        limit: int,
    ) -> list[Any]:
        search = self.client().search
        if callable(search) and not hasattr(search, "memories"):
            payload = search(
                q=query,
                container_tag=container_tag,
                search_mode=search_mode,
                limit=limit,
            )
        else:
            payload = search.memories(
                q=query,
                container_tag=container_tag,
                search_mode=search_mode,
                limit=limit,
            )
        return _rows(payload)

    def add(
        self,
        *,
        content: str,
        container_tag: str,
        metadata: dict[str, Any] | None,
        custom_id: str,
    ) -> bool:
        kwargs: dict[str, Any] = {
            "content": content,
            "container_tag": container_tag,
            "metadata": _metadata(metadata),
        }
        if custom_id:
            kwargs["custom_id"] = custom_id
        try:
            self.client().add(**kwargs)
        except Exception as exc:  # noqa: BLE001
            status = getattr(exc, "status_code", None)
            if status == 409:
                return True
            _LOG.warning("supermemory add failed (%s status=%s)", type(exc).__name__, status)
            return False
        return True

    def list_documents(self, *, container_tag: str, limit: int = 200) -> list[Any]:
        found: list[Any] = []
        page = 1
        while page <= 20:
            listed = self.client().documents.list(
                container_tags=[container_tag],
                limit=limit,
                page=page,
            )
            rows = _rows(listed)
            if not rows:
                break
            found.extend(_dump(row) if not isinstance(row, dict) else row for row in rows)
            pagination = getattr(listed, "pagination", None)
            total_pages = int(getattr(pagination, "total_pages", 1) or 1) if pagination else 1
            if page >= total_pages:
                break
            page += 1
        return found


def build_sdk_client(settings: MemorySettings) -> Any:
    from supermemory import Supermemory

    return Supermemory(
        api_key=settings.api_key or None,
        base_url=settings.base_url,
        timeout=settings.request_timeout_s,
        max_retries=0,
    )


def open_transport(settings: MemorySettings, *, session: Any = None) -> MemoryTransport:
    """SDK when ``supermemory`` imports, otherwise HTTP. Health is always HTTP."""
    http = HttpTransport(
        settings.base_url,
        settings.api_key,
        timeout_s=settings.request_timeout_s,
        session=session,
    )
    try:
        from supermemory import Supermemory  # noqa: F401
    except ImportError:
        _LOG.warning("supermemory SDK missing; using the v3 HTTP client")
        return http
    try:
        return SdkTransport(settings, http=http)
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("supermemory SDK init failed (%s); using HTTP", type(exc).__name__)
        return http
