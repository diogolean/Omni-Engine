# -*- coding: utf-8 -*-
"""``MemoryClient`` for one namespace, with a local ledger and a retry queue."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from modules.memory.ancient import visual_hits
from modules.memory.config import MemorySettings, env_flag, load_settings
from modules.memory.ledger import NamespaceLedger
from modules.memory.similarity import hit_metadata, hit_score, hit_text, lexical_similarity
from modules.memory.transport import MemoryTransport, open_transport
from modules.memory.wsl import ensure_supermemory_running

_LOG = logging.getLogger("omni.memory")
_CUSTOM_ID = re.compile(r"[^A-Za-z0-9_:-]+")


def safe_custom_id(value: str) -> str:
    cleaned = _CUSTOM_ID.sub("-", (value or "").strip()).strip("-")
    return cleaned[:100]


@dataclass(frozen=True)
class AddResult:
    stored_locally: bool
    remote: bool
    queued: bool


class MemoryService:
    """Shared health probe and transport for one process and one server."""

    def __init__(
        self,
        settings: MemorySettings | None = None,
        *,
        transport: MemoryTransport | None = None,
    ) -> None:
        self.settings = settings or load_settings()
        self.transport = transport if transport is not None else open_transport(self.settings)
        self._cached: bool | None = None
        self._cached_at = 0.0
        self._clients: list[MemoryClient] = []
        self._autostart_done = False

    def attach(self, client: MemoryClient) -> None:
        self._clients.append(client)

    def mark_down(self) -> None:
        self._cached = False
        self._cached_at = time.monotonic()

    def health(self, *, force: bool = False) -> bool:
        if self.settings.disabled:
            self.mark_down()
            return False
        now = time.monotonic()
        if (
            not force
            and self._cached is not None
            and now - self._cached_at < self.settings.health_cache_s
        ):
            return self._cached
        self._maybe_autostart()
        try:
            ok = bool(self.transport.health(self.settings.health_timeout_s))
        except Exception:  # noqa: BLE001
            ok = False
        self._cached = ok
        self._cached_at = time.monotonic()
        if ok:
            self.replay_all()
        return ok

    def _maybe_autostart(self) -> None:
        if self._autostart_done or not self.settings.local_server:
            return
        if not env_flag("SUPERMEMORY_AUTOSTART_WSL"):
            return
        self._autostart_done = True
        parsed = urlsplit(self.settings.base_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 6767
        ensure_supermemory_running(host=host, port=port)

    def replay_all(self) -> int:
        sent = 0
        for client in self._clients:
            sent += client.replay_pending()
        return sent

    def sdk_client(self) -> Any | None:
        opener = getattr(self.transport, "client", None)
        if not callable(opener):
            return None
        try:
            return opener()
        except Exception:  # noqa: BLE001
            return None


class MemoryClient:
    """Search and add inside one container tag.

    Every add is written to the local JSONL ledger. A failed remote write is
    queued and sent again on the next healthy call, keyed by ``custom_id``.
    """

    def __init__(
        self,
        namespace: str,
        *,
        channel: str | None = None,
        project: str | None = None,
        purpose: str | None = None,
        service: MemoryService | None = None,
        transport: MemoryTransport | None = None,
        settings: MemorySettings | None = None,
        ledger_dir: Path | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        disabled: bool | None = None,
        health_timeout_s: float | None = None,
        similarity_threshold: float | None = None,
    ) -> None:
        self.namespace = namespace.strip()
        self.channel = (channel or _channel_from_namespace(self.namespace)).strip().lower()
        self.project = (project or self.channel).strip().lower()
        self.purpose = (purpose or "").strip().lower()
        if service is None:
            resolved = settings or load_settings(
                base_url=base_url,
                api_key=api_key,
                disabled=disabled,
                health_timeout_s=health_timeout_s,
                similarity_threshold=similarity_threshold,
            )
            service = MemoryService(resolved, transport=transport)
        self.service = service
        self.ledger = NamespaceLedger(self.namespace, channel=self.channel, directory=ledger_dir)
        self.service.attach(self)

    @property
    def threshold(self) -> float:
        return self.service.settings.similarity_threshold

    def health(self, *, force: bool = False) -> bool:
        return self.service.health(force=force)

    def add(
        self,
        content: str,
        *,
        custom_id: str = "",
        metadata: dict[str, Any] | None = None,
        kind: str = "",
    ) -> AddResult:
        text = (content or "").strip()
        if not text:
            return AddResult(False, False, False)
        token = safe_custom_id(custom_id)
        meta = {
            "project": self.project,
            "channel": self.channel,
            "kind": kind or self.purpose or "memory",
        }
        if metadata:
            meta.update(metadata)
        if token:
            meta.setdefault("custom_id", token)
        record = self.ledger.append(content=text, custom_id=token, metadata=meta)
        if not self.health():
            self.ledger.enqueue(record)
            return AddResult(True, False, True)
        if self._send(record):
            self.ledger.drop_pending(token)
            return AddResult(True, True, False)
        self.ledger.enqueue(record)
        self.service.mark_down()
        return AddResult(True, False, True)

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        search_mode: str = "memories",
    ) -> list[dict[str, Any]]:
        needle = (query or "").strip()
        if not needle:
            return []
        local = visual_hits(self.namespace, needle, channel=self.channel, limit=limit)
        remote: list[Any] = []
        if self.health():
            try:
                remote = self.service.transport.search(
                    query=needle,
                    container_tag=self.namespace,
                    search_mode=search_mode,
                    limit=limit,
                )
            except Exception as exc:  # noqa: BLE001
                _LOG.warning("supermemory search failed (%s)", type(exc).__name__)
                self.service.mark_down()
                remote = self._local_hits(needle, limit=limit)
        else:
            remote = self._local_hits(needle, limit=limit)
        return _merge_hits(local, remote, limit=limit)

    def is_duplicate(self, query: str, *, threshold: float | None = None) -> bool:
        needle = (query or "").strip()
        if not needle:
            return False
        cutoff = self.threshold if threshold is None else float(threshold)
        if self._local_duplicate(needle, cutoff):
            return True
        for hit in visual_hits(self.namespace, needle, channel=self.channel, limit=5):
            if hit_score(hit) >= cutoff:
                return True
        if not self.health():
            return False
        try:
            hits = self.service.transport.search(
                query=needle,
                container_tag=self.namespace,
                search_mode="memories",
                limit=5,
            )
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("supermemory duplicate search failed (%s)", type(exc).__name__)
            self.service.mark_down()
            return False
        if not hits:
            return False
        return max(hit_score(hit) for hit in hits) > cutoff

    def list_documents(self, *, limit: int = 200) -> list[Any]:
        if not self.health():
            return []
        try:
            return list(
                self.service.transport.list_documents(container_tag=self.namespace, limit=limit)
            )
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("supermemory list failed (%s)", type(exc).__name__)
            self.service.mark_down()
            return []

    def replay_pending(self) -> int:
        pending = self.ledger.pending()
        if not pending:
            return 0
        kept: list[dict[str, Any]] = []
        sent = 0
        seen: set[str] = set()
        for row in pending:
            token = str(row.get("custom_id") or "")
            if token and token in seen:
                continue
            if token:
                seen.add(token)
            if self._send(row):
                sent += 1
            else:
                kept.append(row)
        self.ledger.replace_pending(kept)
        return sent

    def _send(self, row: dict[str, Any]) -> bool:
        try:
            return bool(
                self.service.transport.add(
                    content=str(row.get("content") or ""),
                    container_tag=self.namespace,
                    metadata=dict(row.get("metadata") or {}),
                    custom_id=str(row.get("custom_id") or ""),
                )
            )
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("supermemory add failed (%s)", type(exc).__name__)
            return False

    def _local_hits(self, query: str, *, limit: int) -> list[dict[str, Any]]:
        scored: list[tuple[float, dict[str, Any]]] = []
        for row in self.ledger.records():
            text = _record_text(row)
            score = lexical_similarity(query, text)
            if score <= 0:
                continue
            scored.append(
                (
                    score,
                    {
                        "memory": str(row.get("content") or ""),
                        "similarity": score,
                        "metadata": dict(row.get("metadata") or {}),
                        "custom_id": str(row.get("custom_id") or ""),
                    },
                )
            )
        scored.sort(key=lambda item: item[0], reverse=True)
        return [hit for _score, hit in scored[:limit]]

    def _local_duplicate(self, query: str, threshold: float) -> bool:
        for row in self.ledger.records():
            if lexical_similarity(query, _record_text(row)) >= threshold:
                return True
        return False


def _channel_from_namespace(namespace: str) -> str:
    for known in ("ancient_knowledge", "aiwake", "amazing_feed"):
        if namespace.startswith(known):
            return known
    return namespace.split("_", 1)[0] or "shared"


def _record_text(row: dict[str, Any]) -> str:
    parts = [str(row.get("content") or "")]
    meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    for key in ("topic", "text", "quote", "hook", "frame"):
        value = str(meta.get(key) or "").strip()
        if value:
            parts.append(value)
    return " ".join(parts)


def _merge_hits(
    local: list[dict[str, Any]],
    remote: list[Any],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for hit in list(local) + list(remote):
        if isinstance(hit, dict):
            item = hit
        else:
            item = {
                "memory": hit_text(hit),
                "similarity": hit_score(hit),
                "metadata": hit_metadata(hit),
            }
        key = str(item.get("custom_id") or "") or hit_text(item)[:180]
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    merged.sort(key=hit_score, reverse=True)
    return merged[:limit]
