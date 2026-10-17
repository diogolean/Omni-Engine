# -*- coding: utf-8 -*-
"""JSONL ledger of beat visuals that passed or failed QA.

One row per judged still. The next episode of the same topic reads the
rows back so a rejected depiction is named in the next prompt. No network
and no embedding model.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from modules.reel_grounding.config import memory_enabled

_TOKEN = re.compile(r"[a-z0-9]{4,}")
_STOP = frozenset({
    "this", "that", "with", "from", "they", "them", "have", "been", "were",
    "when", "what", "which", "their", "there", "about", "would", "could",
    "ancient", "history", "found", "shows", "show", "still", "into", "over",
})


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN.findall((text or "").lower()) if t not in _STOP}


def _overlap(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


@dataclass
class MemoryHints:
    accepted: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)


def ledger_path(channel: str, root: Path | None = None) -> Path:
    if root is not None:
        path = Path(root) / "visual_memory.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
    from utils.pipeline_paths import review_path

    return review_path(channel or "ancient_knowledge", "visual_memory.jsonl")


def remember(
    *,
    channel: str,
    topic: str,
    spoken: str,
    visual_subject: str,
    passed: bool,
    score: float | None = None,
    rejection: str = "",
    first_pass: bool | None = None,
    root: Path | None = None,
) -> None:
    if not memory_enabled():
        return
    row = {
        "channel": channel or "",
        "topic": " ".join((topic or "").split())[:240],
        "spoken": " ".join((spoken or "").split())[:400],
        "visual_subject": " ".join((visual_subject or "").split())[:400],
        "passed": bool(passed),
        "score": score,
        "rejection": " ".join((rejection or "").split())[:300],
        "first_pass": first_pass,
    }
    try:
        path = ledger_path(channel, root)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        return


def recall(
    *,
    channel: str,
    topic: str,
    spoken: str = "",
    root: Path | None = None,
    limit: int = 400,
) -> MemoryHints:
    if not memory_enabled():
        return MemoryHints()
    try:
        path = ledger_path(channel, root)
        if not path.is_file():
            return MemoryHints()
        lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
    except OSError:
        return MemoryHints()
    want = _tokens(f"{topic} {spoken}")
    accepted: list[str] = []
    rejected: list[str] = []
    for line in reversed(lines):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        if channel and row.get("channel") and row.get("channel") != channel:
            continue
        have = _tokens(f"{row.get('topic') or ''} {row.get('spoken') or ''}")
        same_topic = _overlap(_tokens(topic), _tokens(str(row.get("topic") or "")))
        if same_topic < 0.5 and _overlap(want, have) < 0.34:
            continue
        if row.get("passed") and row.get("visual_subject"):
            text = str(row["visual_subject"])
            if text not in accepted:
                accepted.append(text)
        elif row.get("rejection"):
            text = str(row["rejection"])
            if text not in rejected:
                rejected.append(text)
        if len(accepted) >= 1 and len(rejected) >= 3:
            break
    return MemoryHints(accepted=accepted[:1], rejected=rejected[:3])
