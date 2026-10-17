# -*- coding: utf-8 -*-
"""Aiwake adapter over the shared memory client.

Production history lives in the ``aiwake_production_history`` container.
Channel rules live in ``aiwake_system_rules``. The server URL and key come
from the environment for any host. If the service is down, topic checks use
``history_seeds.json`` and failed writes sit in the shared retry queue.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Sequence

from modules.memory.client import MemoryClient, MemoryService
from modules.memory.config import env_flag, load_settings
from modules.memory.namespaces import AIWAKE_PRODUCTION_HISTORY, AIWAKE_SYSTEM_RULES
from modules.memory.similarity import hit_metadata, hit_score, lexical_similarity
from modules.memory.wsl import ensure_supermemory_running, reset_start_attempt

_LOG = logging.getLogger("aiwake.supermemory")

DEFAULT_BASE_URL = "http://localhost:6767"
HISTORY_CONTAINER = AIWAKE_PRODUCTION_HISTORY
RULES_CONTAINER = AIWAKE_SYSTEM_RULES
LEDGER_FILENAME = "history_seeds.json"
SIMILARITY_THRESHOLD = 0.75
OFFLINE_WARNING = (
    "[OmniEngine] Supermemory offline. Falling back to local JSON ledger."
)
GOLDEN_RULE = (
    "Rule: Strictly maximum 3 hashtags per post. Captions must use double "
    "line breaks. Topics must strictly reflect actual debate content without "
    "hallucinated defaults."
)
_BRIDGE: SupermemoryBridge | None = None


def _load_environment() -> None:
    try:
        from channels_config.aiwake.settings import load_environment
    except ImportError:  # pragma: no cover — standalone extraction
        return
    try:
        load_environment()
    except Exception:  # noqa: BLE001 — missing dotenv must not abort a debate
        return


def _default_ledger_path() -> Path:
    try:
        from channels_config.aiwake.settings import resolve_store_dir

        return resolve_store_dir() / LEDGER_FILENAME
    except Exception:  # noqa: BLE001
        return Path(__file__).resolve().parents[1] / "store" / LEDGER_FILENAME


def _model_key(target_model: str) -> str:
    return (target_model or "").strip().lower()


def _hit_target_model(hit: Any) -> str:
    return _model_key(str(hit_metadata(hit).get("target_model") or ""))


def session_memory_text(
    *,
    session_id: str,
    topic: str,
    hook: str,
    quote: str,
    target_model: str = "",
    claims: Sequence[dict[str, Any]] = (),
) -> str:
    claim_lines = "\n".join(
        f"Turn {int(item.get('turn_index', 0)) + 1}: {str(item.get('text') or '').strip()}"
        for item in claims
        if str(item.get("text") or "").strip()
    )
    base = (
        f"Aiwake approved session {session_id}.\n"
        f"Target model: {target_model}\n"
        f"Topic: {topic}\n"
        f"Opening hook: {hook}\n"
        f"Core quote: {quote}"
    )
    return f"{base}\nSession claim ledger:\n{claim_lines}" if claim_lines else base


class SupermemoryBridge:
    """Aiwake history and rules on top of :class:`MemoryClient`."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        ledger_path: Path | None = None,
        similarity_threshold: float = SIMILARITY_THRESHOLD,
        force_offline: bool = False,
        health_timeout_s: float = 1.5,
        service: MemoryService | None = None,
    ) -> None:
        _load_environment()
        self._forced_off = bool(force_offline)
        settings = load_settings(
            base_url=base_url,
            api_key=api_key,
            disabled=True if force_offline else None,
            health_timeout_s=health_timeout_s,
            similarity_threshold=similarity_threshold,
        )
        self.base_url = settings.base_url
        self.api_key = settings.api_key
        self._persist_durable = ledger_path is None
        self.ledger_path = Path(ledger_path) if ledger_path is not None else _default_ledger_path()
        self.similarity_threshold = float(similarity_threshold)
        self.history_container = HISTORY_CONTAINER
        self.rules_container = RULES_CONTAINER
        ledger_dir = None if ledger_path is None else self.ledger_path.parent / "memory"
        if service is None:
            service = MemoryService(settings)
        self.service = service
        self._history = MemoryClient(
            HISTORY_CONTAINER,
            channel="aiwake",
            project="aiwake",
            purpose="production_history",
            service=service,
            ledger_dir=ledger_dir,
        )
        self._rules = MemoryClient(
            RULES_CONTAINER,
            channel="aiwake",
            project="aiwake",
            purpose="system_rules",
            service=service,
            ledger_dir=ledger_dir,
        )
        if not self.is_active:
            _LOG.warning("%s (%s)", OFFLINE_WARNING, self.base_url)

    @property
    def is_active(self) -> bool:
        if self._forced_off:
            return False
        return self.service.health()

    def _client_or_none(self) -> Any | None:
        """SDK handle for callers that still list documents themselves."""
        if not self.is_active:
            return None
        return self.service.sdk_client()

    def remote_session_ids(self) -> set[str]:
        if not self.is_active:
            return set()
        found: set[str] = set()
        try:
            documents = self._history.list_documents()
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("could not list existing Supermemory documents (%s)", type(exc).__name__)
            return found
        for item in documents:
            meta = hit_metadata(item)
            if meta.get("session_id"):
                found.add(str(meta["session_id"]))
        return found

    def _load_ledger(self) -> dict[str, Any]:
        path = self.ledger_path
        try:
            from modules.durable_store import hydrate_state_file

            path = hydrate_state_file(path)
        except Exception:
            pass
        if not Path(path).is_file():
            return {"sessions": []}
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("history ledger unreadable (%s); starting empty", type(exc).__name__)
            return {"sessions": []}
        if isinstance(raw, list):
            return {"sessions": [row for row in raw if isinstance(row, dict)]}
        sessions = raw.get("sessions") if isinstance(raw, dict) else []
        if not isinstance(sessions, list):
            sessions = []
        return {"sessions": [row for row in sessions if isinstance(row, dict)]}

    def _save_ledger(self, sessions: list[dict[str, Any]]) -> None:
        payload = {"sessions": sessions}
        if self._persist_durable:
            try:
                from modules.durable_store import write_state_json

                dest = write_state_json("aiwake", LEDGER_FILENAME, payload)
                self.ledger_path = Path(dest)
                return
            except Exception:
                pass
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.ledger_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(self.ledger_path)

    def ledger_sessions(self) -> list[dict[str, Any]]:
        return list(self._load_ledger().get("sessions") or [])

    def _local_duplicate(self, topic_query: str, *, target_model: str = "") -> bool:
        query = (topic_query or "").strip()
        wanted = _model_key(target_model)
        if not query:
            return False
        for row in self.ledger_sessions():
            row_model = _model_key(str(row.get("target_model") or ""))
            if row_model != wanted:
                continue
            for field in ("topic", "hook", "quote"):
                candidate = str(row.get(field) or "").strip()
                if candidate and lexical_similarity(query, candidate) >= self.similarity_threshold:
                    return True
        return False

    def check_topic_similarity(self, topic_query: str, target_model: str = "") -> bool:
        """True when this target model has already faced a near-duplicate topic."""
        query = (topic_query or "").strip()
        wanted = _model_key(target_model)
        if not query:
            return False
        if self._local_duplicate(query, target_model=wanted):
            return True
        if not self.is_active or not wanted:
            return False
        hits = self._history.search(query, search_mode="memories")
        if not hits:
            hits = self._history.search(query, search_mode="documents")
        scoped = [hit for hit in hits if _hit_target_model(hit) == wanted]
        if not scoped:
            return False
        top = max(hit_score(hit) for hit in scoped)
        return top > self.similarity_threshold

    def remember_approved_session(
        self,
        session_id: str,
        topic: str,
        hook: str,
        quote: str,
        *,
        remote: bool = True,
        target_model: str = "",
        claims: Sequence[dict[str, Any]] = (),
    ) -> bool:
        """Persist an approved debate footprint. Always updates the local ledger."""
        sid = (session_id or "").strip()
        topic_text = (topic or "").strip()
        hook_text = (hook or "").strip()
        quote_text = (quote or "").strip()
        if not sid and not topic_text:
            return False

        sessions = self.ledger_sessions()
        existing = next((row for row in sessions if str(row.get("session_id") or "") == sid), None)
        record = {
            "session_id": sid,
            "topic": topic_text,
            "hook": hook_text,
            "quote": quote_text,
            "target_model": _model_key(target_model),
            "claims": [dict(item) for item in claims],
        }
        if existing is None:
            sessions.append(record)
        else:
            existing.update(record)
        try:
            self._save_ledger(sessions)
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("local history ledger write failed (%s)", type(exc).__name__)

        if remote:
            custom_id = safe_session_id(sid, topic_text)
            self._history.add(
                session_memory_text(
                    session_id=sid,
                    topic=topic_text,
                    hook=hook_text,
                    quote=quote_text,
                    target_model=_model_key(target_model),
                    claims=claims,
                ),
                custom_id=custom_id,
                kind="approved_session",
                metadata={
                    "session_id": sid,
                    "kind": "approved_session",
                    "topic": topic_text[:200],
                    "target_model": _model_key(target_model),
                    "custom_id": custom_id,
                },
            )
        return True

    def remember_system_rule(self, rule: str = GOLDEN_RULE) -> bool:
        text = (rule or GOLDEN_RULE).strip()
        if not text:
            return False
        result = self._rules.add(
            text,
            custom_id="aiwake-system-rule",
            kind="system_rule",
            metadata={
                "kind": "system_rule",
                "rule_id": "caption_contract",
                "custom_id": "aiwake-system-rule",
            },
        )
        return result.remote


def safe_session_id(session_id: str, topic: str) -> str:
    raw = f"aiwake-{session_id}" if session_id else f"aiwake-topic-{topic[:40]}"
    return re.sub(r"[^A-Za-z0-9_:-]+", "-", raw)[:100]


def get_bridge() -> SupermemoryBridge:
    """Process-wide bridge. Isolated during pytest unless SUPERMEMORY_IN_TESTS=1."""
    global _BRIDGE
    if _BRIDGE is None:
        in_pytest = bool(os.getenv("PYTEST_CURRENT_TEST"))
        force_offline = env_flag("SUPERMEMORY_DISABLED") or (
            in_pytest and not env_flag("SUPERMEMORY_IN_TESTS")
        )
        _BRIDGE = SupermemoryBridge(force_offline=force_offline)
    return _BRIDGE


def reset_bridge() -> None:
    """Drop the cached singleton (tests)."""
    global _BRIDGE
    _BRIDGE = None
    reset_start_attempt()


def check_topic_similarity(topic_query: str, target_model: str = "") -> bool:
    try:
        return get_bridge().check_topic_similarity(topic_query, target_model=target_model)
    except Exception as exc:  # noqa: BLE001 — pipeline must never crash
        _LOG.warning("topic similarity check failed (%s); treating as unique", type(exc).__name__)
        return False


def remember_approved_session(
    session_id: str,
    topic: str,
    hook: str,
    quote: str,
    target_model: str = "",
    claims: Sequence[dict[str, Any]] = (),
) -> bool:
    try:
        return get_bridge().remember_approved_session(
            session_id,
            topic,
            hook,
            quote,
            target_model=target_model,
            claims=claims,
        )
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("remember_approved_session failed (%s)", type(exc).__name__)
        return False


__all__ = [
    "GOLDEN_RULE",
    "HISTORY_CONTAINER",
    "LEDGER_FILENAME",
    "OFFLINE_WARNING",
    "RULES_CONTAINER",
    "SIMILARITY_THRESHOLD",
    "SupermemoryBridge",
    "check_topic_similarity",
    "ensure_supermemory_running",
    "get_bridge",
    "lexical_similarity",
    "remember_approved_session",
    "reset_bridge",
    "session_memory_text",
]
