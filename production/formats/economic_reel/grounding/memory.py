# -*- coding: utf-8 -*-
"""JSONL ledger of beat visuals and, for Ancient Knowledge, narrative angles.

One row per judged still. The next episode of the same topic reads the
rows back so a rejected depiction is named in the next prompt. Angle rows
(``kind`` = ``angle``) share the file and are ignored by visual recall.
No network and no embedding model.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from production.formats.economic_reel.grounding.config import angle_memory_enabled, memory_enabled

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
    from shared.paths import review_path

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
        if row.get("kind") == "angle":
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


# ---------------------------------------------------------------------------
# Per-topic narrative angles (same ledger, kind="angle")
# ---------------------------------------------------------------------------

_ANGLE_KIND = "angle"
_DEFAULT_ANGLE_HISTORY = 6


def _norm_angle(text: str) -> str:
    return " ".join((text or "").lower().split())


def _topic_slug(topic: str) -> str:
    from production.media.text_utils import subject_slug

    return subject_slug(topic or "")


def _same_topic(topic: str, row: dict) -> bool:
    slug = _topic_slug(topic)
    row_slug = str(row.get("topic_slug") or "")
    if row_slug and slug and row_slug == slug:
        return True
    row_topic = str(row.get("topic") or "")
    if not row_topic:
        return False
    return _overlap(_tokens(topic), _tokens(row_topic)) >= 0.5


@dataclass
class AnglePlan:
    """Angle locked for this episode, plus the prompt text for the script call."""

    angle: str = ""
    prior_block: str = ""
    prompt_block: str = ""


def recent_angles(
    *,
    channel: str,
    topic: str,
    root: Path | None = None,
    limit: int = _DEFAULT_ANGLE_HISTORY,
    scan: int = 800,
) -> list[dict]:
    """Newest-first angle rows for this topic. Empty when the flag is off."""
    if not angle_memory_enabled():
        return []
    try:
        path = ledger_path(channel, root)
        if not path.is_file():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()[-scan:]
    except OSError:
        return []
    found: list[dict] = []
    for line in reversed(lines):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict) or row.get("kind") != _ANGLE_KIND:
            continue
        if channel and row.get("channel") and row.get("channel") != channel:
            continue
        if not _same_topic(topic, row):
            continue
        found.append(row)
        if len(found) >= max(1, int(limit)):
            break
    return found


def choose_angle(pool: list[str], used_newest_first: list[str], *, salt: str = "") -> str:
    """Pick a pool entry that is not in the recent window.

    The same history and salt always return the same entry, so the caption
    prompt and the sequence prompt in one episode lock the same lens.
    When every entry was used inside the window, the least recent one returns.
    """
    cleaned = [a.strip() for a in pool if a and str(a).strip()]
    if not cleaned:
        return ""
    used_norm: list[str] = []
    for raw in used_newest_first:
        key = _norm_angle(str(raw))
        if key and key not in used_norm:
            used_norm.append(key)
    fresh = [a for a in cleaned if _norm_angle(a) not in set(used_norm)]
    if not fresh:
        rank = {key: index for index, key in enumerate(used_norm)}
        return max(cleaned, key=lambda item: rank.get(_norm_angle(item), len(used_norm)))
    if len(fresh) == 1:
        return fresh[0]
    digest = hashlib.sha256(
        f"{salt}|{len(used_norm)}".encode("utf-8")
    ).hexdigest()
    return fresh[int(digest[:8], 16) % len(fresh)]


def _format_prior(rows: list[dict]) -> str:
    lines: list[str] = []
    for row in rows:
        angle = str(row.get("angle") or "").strip()
        hook = str(row.get("hook") or "").strip()
        if angle and hook:
            lines.append(f"  - {angle} | hook: {hook}")
        elif angle:
            lines.append(f"  - {angle}")
        elif hook:
            lines.append(f"  - hook: {hook}")
    if not lines:
        return ""
    body = "\n".join(lines)
    return (
        "PRIOR ANGLES ON THIS TOPIC — take a NEW angle. "
        "Do not reuse these frames, hooks, sub-questions, or evidence focuses:\n"
        f"{body}\n"
    )


def plan_episode_angle(
    *,
    channel: str,
    topic: str,
    pool: list[str],
    root: Path | None = None,
    last_n: int = _DEFAULT_ANGLE_HISTORY,
) -> AnglePlan:
    """Build the block appended to the existing script prompt. No model call."""
    if not angle_memory_enabled():
        return AnglePlan()
    rows = recent_angles(channel=channel, topic=topic, root=root, limit=last_n)
    chosen = choose_angle(
        list(pool or []),
        [str(row.get("angle") or "") for row in rows],
        salt=_topic_slug(topic),
    )
    prior = _format_prior(rows)
    parts: list[str] = []
    if prior:
        parts.append(prior.rstrip())
    if chosen:
        used_now = {_norm_angle(str(row.get("angle") or "")) for row in rows}
        if _norm_angle(chosen) in used_now:
            lens_note = (
                "This frame is the oldest one on the list. Keep the hook, "
                "the sub-question, and the evidence focus different from every line above."
            )
        else:
            lens_note = (
                "Open from this lens. It must differ from every prior angle on this topic."
            )
        parts.append(f"MANDATORY ANGLE FOR THIS EPISODE: {chosen}\n{lens_note}")
    return AnglePlan(
        angle=chosen,
        prior_block=prior,
        prompt_block="\n".join(parts).strip(),
    )


def opening_hook(script: str, limit: int = 180) -> str:
    """First spoken sentence, used as the recorded hook. No model call."""
    text = " ".join((script or "").split())
    text = re.sub(r"\[ACT\s+\d+\]", " ", text, flags=re.IGNORECASE)
    text = " ".join(text.split())
    if not text:
        return ""
    sentence = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0]
    return sentence[:limit].strip()


def remember_angle(
    *,
    channel: str,
    topic: str,
    angle: str,
    hook: str = "",
    root: Path | None = None,
) -> None:
    """Append one angle row. A repeat of the newest row for this topic is skipped."""
    if not angle_memory_enabled():
        return
    angle_text = " ".join((angle or "").split())[:300]
    hook_text = " ".join((hook or "").split())[:180]
    if not angle_text and not hook_text:
        return
    prior = recent_angles(channel=channel, topic=topic, root=root, limit=1)
    if prior:
        top = prior[0]
        same_angle = _norm_angle(str(top.get("angle") or "")) == _norm_angle(angle_text)
        same_hook = _norm_angle(str(top.get("hook") or "")) == _norm_angle(hook_text)
        if same_angle and same_hook:
            return
    row = {
        "kind": _ANGLE_KIND,
        "channel": channel or "",
        "topic": " ".join((topic or "").split())[:240],
        "topic_slug": _topic_slug(topic),
        "angle": angle_text,
        "hook": hook_text,
    }
    try:
        path = ledger_path(channel, root)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        return
