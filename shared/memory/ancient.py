# -*- coding: utf-8 -*-
"""Read-only view of Ancient Knowledge reel memory.

``outputs/<channel>/_review/visual_memory.jsonl`` is written by the reel
pipeline (one JSON object per line: kind ``visual`` or ``angle``, a topic
slug, text or frame, and a QA status). This adapter does not create or
modify that file. Search maps rows into:

- ``ancient_knowledge_reel_angles``
- ``ancient_knowledge_reel_visuals``
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shared.memory.similarity import lexical_similarity

_LOG = logging.getLogger("omni.memory")

ANCIENT_KNOWLEDGE_REEL_ANGLES = "ancient_knowledge_reel_angles"
ANCIENT_KNOWLEDGE_REEL_VISUALS = "ancient_knowledge_reel_visuals"

_KIND_BY_NAMESPACE = {
    ANCIENT_KNOWLEDGE_REEL_ANGLES: "angle",
    ANCIENT_KNOWLEDGE_REEL_VISUALS: "visual",
}


@dataclass(frozen=True)
class VisualMemoryRecord:
    kind: str
    topic: str
    text: str
    frame: str
    qa_status: str

    def searchable_text(self) -> str:
        return " ".join(part for part in (self.topic, self.text, self.frame) if part).strip()


def visual_memory_path(channel: str = "ancient_knowledge") -> Path:
    """Path of the review JSONL. Does not create directories."""
    from shared.paths import page_outputs_dir

    slug = (channel or "ancient_knowledge").strip().lower() or "ancient_knowledge"
    return page_outputs_dir(slug) / "_review" / "visual_memory.jsonl"


def _first(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def parse_visual_record(row: dict[str, Any]) -> VisualMemoryRecord | None:
    kind = _first(row, "kind", "type").lower()
    if kind not in {"visual", "angle"}:
        return None
    return VisualMemoryRecord(
        kind=kind,
        topic=_first(row, "topic", "topic_slug", "slug"),
        text=_first(row, "text", "body", "caption"),
        frame=_first(row, "frame", "frame_path", "image"),
        qa_status=_first(row, "qa_status", "qa", "status"),
    )


def load_visual_memory(channel: str = "ancient_knowledge") -> list[VisualMemoryRecord]:
    """Read the review JSONL. Missing or unreadable files yield an empty list."""
    path = visual_memory_path(channel)
    if not path.is_file():
        return []
    records: list[VisualMemoryRecord] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        _LOG.warning("visual memory unreadable (%s)", type(exc).__name__)
        return []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            raw = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, dict):
            continue
        record = parse_visual_record(raw)
        if record is not None:
            records.append(record)
    return records


def visual_hits(
    namespace: str,
    query: str,
    *,
    channel: str = "ancient_knowledge",
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Lexical hits from the review file for one Ancient Knowledge namespace."""
    kind = _KIND_BY_NAMESPACE.get(namespace)
    if kind is None:
        return []
    needle = (query or "").strip()
    if not needle:
        return []
    scored: list[tuple[float, VisualMemoryRecord]] = []
    for record in load_visual_memory(channel):
        if record.kind != kind:
            continue
        score = lexical_similarity(needle, record.searchable_text())
        if score <= 0:
            continue
        scored.append((score, record))
    scored.sort(key=lambda item: item[0], reverse=True)
    hits: list[dict[str, Any]] = []
    for score, record in scored[:limit]:
        hits.append(
            {
                "memory": record.text or record.frame or record.topic,
                "similarity": score,
                "metadata": {
                    "project": "ancient_knowledge",
                    "channel": "reel",
                    "kind": record.kind,
                    "topic": record.topic,
                    "qa_status": record.qa_status,
                    "frame": record.frame,
                    "source": "visual_memory.jsonl",
                },
            }
        )
    return hits
