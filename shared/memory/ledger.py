# -*- coding: utf-8 -*-
"""Per-namespace JSONL ledger and a queue of writes the service has not accepted."""

from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOG = logging.getLogger("omni.memory")
_LOCKS: dict[str, threading.Lock] = {}
_SAFE = re.compile(r"[^A-Za-z0-9_:-]+")


def _lock_for(key: str) -> threading.Lock:
    lock = _LOCKS.get(key)
    if lock is None:
        lock = threading.Lock()
        _LOCKS[key] = lock
    return lock


def _safe_name(namespace: str) -> str:
    cleaned = _SAFE.sub("_", namespace).strip("_")
    return (cleaned or "memory")[:100]


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        _LOG.warning("memory ledger unreadable (%s)", type(exc).__name__)
        return []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            raw = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(raw, dict):
            rows.append(raw)
    return rows


def _atomic_write(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)


class NamespaceLedger:
    """Local fallback for one container tag.

    ``directory`` keeps the files in a test or explicit folder. When it is
    omitted, files live in the channel durable store under ``memory/``.
    """

    def __init__(
        self,
        namespace: str,
        *,
        channel: str,
        directory: Path | None = None,
    ) -> None:
        self.namespace = namespace
        self.channel = channel
        self._directory = Path(directory) if directory is not None else None
        self._filename = f"{_safe_name(namespace)}.jsonl"
        self._pending_name = f"{_safe_name(namespace)}.pending.jsonl"

    def _resolve(self, name: str) -> Path:
        if self._directory is not None:
            self._directory.mkdir(parents=True, exist_ok=True)
            return self._directory / name
        from shared.store import hydrate_state_file, local_state_path

        path = local_state_path(self.channel, Path("memory") / name, create_parent=True)
        return hydrate_state_file(path)

    def _mirror(self, path: Path) -> None:
        if self._directory is not None:
            return
        try:
            from shared.store import sync_state_file

            sync_state_file(path)
        except Exception as exc:  # noqa: BLE001 — the local write already landed
            _LOG.warning("memory ledger mirror failed (%s)", type(exc).__name__)

    @property
    def path(self) -> Path:
        return self._resolve(self._filename)

    @property
    def pending_path(self) -> Path:
        return self._resolve(self._pending_name)

    def records(self) -> list[dict[str, Any]]:
        return _read_jsonl(self.path)

    def pending(self) -> list[dict[str, Any]]:
        return _read_jsonl(self.pending_path)

    def append(self, *, content: str, custom_id: str, metadata: dict[str, Any]) -> dict[str, Any]:
        record = {
            "namespace": self.namespace,
            "custom_id": custom_id,
            "content": content,
            "metadata": metadata,
            "written_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        key = str(self.path)
        with _lock_for(key):
            path = self.path
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._mirror(path)
        return record

    def enqueue(self, record: dict[str, Any]) -> None:
        """Queue a failed write. The same ``custom_id`` replaces an older row."""
        custom_id = str(record.get("custom_id") or "")
        key = str(self.pending_path)
        with _lock_for(key):
            rows = self.pending()
            if custom_id:
                rows = [row for row in rows if str(row.get("custom_id") or "") != custom_id]
            rows.append(dict(record))
            path = self.pending_path
            _atomic_write(path, rows)
            self._mirror(path)

    def replace_pending(self, rows: list[dict[str, Any]]) -> None:
        key = str(self.pending_path)
        with _lock_for(key):
            path = self.pending_path
            _atomic_write(path, rows)
            self._mirror(path)

    def drop_pending(self, custom_id: str) -> None:
        if not custom_id:
            return
        kept = [row for row in self.pending() if str(row.get("custom_id") or "") != custom_id]
        self.replace_pending(kept)
