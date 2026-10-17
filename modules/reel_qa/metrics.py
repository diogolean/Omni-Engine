# -*- coding: utf-8 -*-
"""First-pass QA hit rate for one episode.

A hit is a freshly generated still whose first scored verdict passed.
Cached reuse and a judge that never returned a score are left out of the
denominator, so the rate measures the prompt, not a flaky model.
"""
from __future__ import annotations

from typing import Any


def first_pass_summary(beats: list[dict[str, Any]] | None) -> dict[str, Any]:
    scored = [b for b in (beats or []) if b.get("first_pass") is not None]
    hits = sum(1 for b in scored if b.get("first_pass") is True)
    total = len(scored)
    rate = (hits / total) if total else None
    return {
        "first_pass_hits": hits,
        "first_pass_total": total,
        "first_pass_hit_rate": rate,
    }


def format_first_pass(summary: dict[str, Any]) -> str:
    rate = summary.get("first_pass_hit_rate")
    hits = int(summary.get("first_pass_hits") or 0)
    total = int(summary.get("first_pass_total") or 0)
    if rate is None:
        return f"[VisualQA] first_pass_hit_rate=n/a (0 scored, {hits}/{total})"
    return f"[VisualQA] first_pass_hit_rate={float(rate):.2f} ({hits}/{total})"
