# -*- coding: utf-8 -*-
"""Publish decision shared by Post Planner and YouTube."""
from __future__ import annotations

from typing import Any

# Generated still reels and footage reels. Any post type containing REEL
# is treated the same by ``is_reel_post_type``.
REEL_POST_TYPES = frozenset({
    "ECONOMIC_REEL",
    "WAN_REEL",
    "ECONOMIC_REEL_LOFI",
    "DYNAMIC_REEL",
    "SEQUENCE_REEL",
    "REFERENCE_BASED_REELS",
})


def is_reel_post_type(post_type: str | None) -> bool:
    token = str(post_type or "").strip().upper()
    if not token:
        return False
    return token in REEL_POST_TYPES or "REEL" in token


def report_allows_publish(report: dict[str, Any] | None) -> bool:
    if not report:
        return False
    return str(report.get("verdict") or "").strip().lower() == "pass"


def planner_row_allowed(*, post_type: str, report: dict[str, Any] | None) -> bool:
    """Post Planner may receive a reel only when QA verdict is pass."""
    if not is_reel_post_type(post_type):
        return True
    return report_allows_publish(report)


def row_publishable(row: dict[str, Any] | None) -> bool:
    """YouTube and other exporters: reel rows need ``qa_verdict=pass``."""
    data = row or {}
    post_type = str(data.get("post_type") or "")
    verdict = str(data.get("qa_verdict") or "").strip().lower()
    if is_reel_post_type(post_type):
        return verdict == "pass"
    if verdict:
        return verdict == "pass"
    return True
