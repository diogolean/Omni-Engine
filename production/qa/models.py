# -*- coding: utf-8 -*-
"""Result objects for one still and for one compiled reel."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _attempt_log(attempts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per generation. The image name is the file the judge saw."""
    rows: list[dict[str, Any]] = []
    for item in attempts:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "attempt": item.get("attempt"),
                "image_name": str(item.get("image_name") or ""),
                "prompt": " ".join(str(item.get("prompt") or "").split())[:1500],
                "rejection_reason": " ".join(str(item.get("rejection_reason") or "").split())[:400],
                "relevance_score": item.get("relevance_score"),
                "qa_status": str(item.get("qa_status") or ""),
            }
        )
    return rows


def preserve_judged_still(row: dict[str, Any], substitute: Path | str) -> None:
    """Keep the picture the judge scored when a card is offered instead.

    ``image_path`` stays the judged file. The card is named apart from it.
    """
    row["judged_image_path"] = str(row.get("image_path") or "")
    row["substitute_name"] = Path(substitute).name
    row["passed"] = False
    row["reuse"] = "fallback"


@dataclass
class StillGateResult:
    """Outcome of generate → score → retry.

    Iterates as ``(path, extra_generations)`` so older callers keep working.
    ``publishable`` is false when the still does not match the spoken line
    or when the vision judge could not run.
    """

    path: Path | None
    extra_generations: int
    publishable: bool
    blocked_reason: str = ""
    attempts: list[dict[str, Any]] = field(default_factory=list)
    verdict: dict[str, Any] = field(default_factory=dict)
    # True when the first scored verdict passed. None when the judge never
    # returned a score (that attempt is left out of the hit rate).
    first_pass: bool | None = None

    def __iter__(self):
        yield self.path
        yield self.extra_generations

    def beat_row(self, *, index: int, text: str) -> dict[str, Any]:
        last = self.attempts[-1] if self.attempts else {}
        score = last.get("relevance_score")
        return {
            "index": int(index),
            "text": text,
            "image_path": str(self.path) if self.path else "",
            "score": score,
            "passed": bool(self.publishable),
            "reason": self.blocked_reason or last.get("rejection_reason") or "",
            "provider": last.get("provider"),
            "model_id": last.get("model_id"),
            "qa_status": last.get("qa_status") or "",
            "attempts": len(self.attempts),
            "attempt_log": _attempt_log(self.attempts),
            "first_pass": self.first_pass,
        }
