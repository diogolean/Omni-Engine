"""Publisher interface: ``publish(content, account)`` with retries and idempotency."""

from __future__ import annotations

import json
import os
import time
from abc import ABC, abstractmethod
from dataclasses import asdict
from pathlib import Path
from typing import Callable

import requests

from omni.models import Account, Content, PublishResult

# Status codes worth another attempt. 401 is not here: a bad token will not
# succeed until someone rotates it.
TRANSIENT_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


class TransientPublishError(Exception):
    """A publish failed before the platform accepted it. Safe to retry."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class IdempotencyStore:
    """Remember successful publishes so a replay does not post twice."""

    def get(self, key: str) -> PublishResult | None:
        raise NotImplementedError

    def put(self, result: PublishResult) -> None:
        raise NotImplementedError


class MemoryIdempotencyStore(IdempotencyStore):
    def __init__(self) -> None:
        self._rows: dict[str, PublishResult] = {}

    def get(self, key: str) -> PublishResult | None:
        return self._rows.get(key)

    def put(self, result: PublishResult) -> None:
        self._rows[result.idempotency_key] = result


class FileIdempotencyStore(IdempotencyStore):
    """JSON ledger under the outputs tree (never the repo root)."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def get(self, key: str) -> PublishResult | None:
        raw = self._read().get(key)
        if not isinstance(raw, dict):
            return None
        return PublishResult(
            ok=bool(raw.get("ok")),
            platform=str(raw.get("platform") or ""),
            idempotency_key=str(raw.get("idempotency_key") or key),
            external_id=raw.get("external_id"),
            skipped=bool(raw.get("skipped")),
            duplicate=bool(raw.get("duplicate")),
            error=raw.get("error"),
        )

    def put(self, result: PublishResult) -> None:
        data = self._read()
        data[result.idempotency_key] = asdict(result)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")

    def _read(self) -> dict:
        if not self.path.is_file():
            return {}
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return loaded if isinstance(loaded, dict) else {}


def default_ledger_path() -> Path:
    override = (os.getenv("OMNI_LEDGER_PATH") or "").strip()
    if override:
        return Path(override).expanduser()
    root = (os.getenv("OUTPUT_PATH") or "").strip()
    base = Path(root).expanduser() if root else Path(__file__).resolve().parents[1] / "outputs"
    return base / "_omni" / "publish_ledger.json"


class Publisher(ABC):
    """Common publish contract.

    Subclasses implement :meth:`publish_once`. This class:

    * skips inactive accounts (no network)
    * returns the stored result when ``idempotency_key`` was already published
    * retries :class:`TransientPublishError` with exponential backoff
    """

    platform: str = ""

    def __init__(
        self,
        *,
        store: IdempotencyStore | None = None,
        max_retries: int = 3,
        backoff_base_sec: float = 10.0,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.store = store if store is not None else FileIdempotencyStore(default_ledger_path())
        self.max_retries = max(1, int(max_retries))
        self.backoff_base_sec = float(backoff_base_sec)
        self.sleeper = sleeper

    def publish(self, content: Content, account: Account) -> PublishResult:
        key = (content.idempotency_key or "").strip()
        if not key:
            raise ValueError("content.idempotency_key is required")
        platform = account.platform or self.platform

        if not account.active:
            return PublishResult(
                ok=True,
                skipped=True,
                platform=platform,
                idempotency_key=key,
                error="inactive",
            )

        prior = self.store.get(key)
        if prior is not None and prior.ok and prior.external_id:
            return PublishResult(
                ok=True,
                skipped=True,
                duplicate=True,
                platform=prior.platform or platform,
                idempotency_key=key,
                external_id=prior.external_id,
            )

        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                result = self.publish_once(content, account)
            except TransientPublishError as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                self.sleeper(self.backoff_base_sec * (2 ** (attempt - 1)))
                continue
            if result.ok and result.external_id and not result.skipped:
                self.store.put(result)
            return result

        return PublishResult(
            ok=False,
            platform=platform,
            idempotency_key=key,
            error=str(last_error) if last_error else "publish failed",
        )

    @abstractmethod
    def publish_once(self, content: Content, account: Account) -> PublishResult:
        """Perform one attempt. Raise TransientPublishError to retry."""


class PostJsonPublisher(Publisher):
    """Small HTTP publisher used by tests and as the retry reference.

    Posts ``content`` as JSON. 408/429/5xx raise :class:`TransientPublishError`.
    Other 4xx return a failed result and are not retried.
    """

    platform = "http"

    def __init__(self, url: str, session: requests.Session | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.url = url
        self.session = session or requests.Session()

    def publish_once(self, content: Content, account: Account) -> PublishResult:
        token = ""
        if account.token_env:
            token = os.getenv(account.token_env, "")
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            response = self.session.post(
                self.url,
                json={
                    "idempotency_key": content.idempotency_key,
                    "account_id": account.account_id,
                    "title": content.title,
                    "body": content.body,
                },
                headers=headers,
                timeout=30,
            )
        except requests.RequestException as exc:
            raise TransientPublishError(str(exc)) from exc

        if response.status_code in TRANSIENT_STATUS:
            raise TransientPublishError(
                f"HTTP {response.status_code}",
                status_code=response.status_code,
            )
        if response.status_code >= 400:
            return PublishResult(
                ok=False,
                platform=account.platform or self.platform,
                idempotency_key=content.idempotency_key,
                error=f"HTTP {response.status_code}",
            )
        payload = response.json() if response.content else {}
        external_id = str(payload.get("id") or "")
        return PublishResult(
            ok=bool(external_id),
            platform=account.platform or self.platform,
            idempotency_key=content.idempotency_key,
            external_id=external_id or None,
            error=None if external_id else "response missing id",
        )
