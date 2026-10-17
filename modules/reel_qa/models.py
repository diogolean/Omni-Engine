# -*- coding: utf-8 -*-
"""Result objects for one still and for one compiled reel."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


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
        }
