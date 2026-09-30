# -*- coding: utf-8 -*-
"""Write production status, park non-ready captions, and generate captions v3.

    python -m channels_config.aiwake.tools.repair_captions

Does not publish. Saves the library every 10 ready entries. Resumes rows that
already have a passing captions_v3 pack.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from channels_config.aiwake.tools.caption_generator import (
    CaptionAuthError,
    CaptionHistory,
    CaptionRunAborted,
    apply_caption_pack,
    generate_caption_pack,
)
from channels_config.aiwake.tools.production_status import (
    SPEAKER_RELABELED_SESSIONS,
    park_non_ready,
    posting_order,
    stamp_production_status,
)
from channels_config.aiwake.tools.validate_aiwake_captions import entry_failures, validate_library
from modules.distribution_contract import content_library_path, load_distribution_library, save_distribution_library

CHANNEL = "aiwake"
_LIBRARY = content_library_path(CHANNEL)
_SCOPE = _LIBRARY.parent / "caption_scope.json"

_CAPTION_KEYS = {
    "final_caption",
    "humanized_caption",
    "post_planner_caption",
    "tiktok_caption",
    "facebook_caption",
    "linkedin_caption",
    "production_status",
    "production_status_reason",
    "production_status_source",
    "caption_qa",
    "legacy_captions",
}
_BASE_KEYS = {"title", "caption", "hashtags"}
_OVERRIDE_KEYS = {"caption", "title", "description", "ai_generated"}


def _backup(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = path.parent / "backups" / f"content_library.{stamp}.bak.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, dest)
    original = json.loads(path.read_text(encoding="utf-8"))
    copied = json.loads(dest.read_text(encoding="utf-8"))
    if len(copied) != len(original):
        raise RuntimeError(f"backup count {len(copied)} != {len(original)}")
    return dest


def _already_ok(row: dict[str, Any]) -> bool:
    qa = row.get("caption_qa") if isinstance(row.get("caption_qa"), dict) else {}
    if str(qa.get("status") or "") != "ok":
        return False
    if str(qa.get("generator") or "") != "captions_v3":
        return False
    if not str(qa.get("model") or "").strip():
        return False
    return not entry_failures(row)


def _note_from_row(history: CaptionHistory, row: dict[str, Any]) -> None:
    from channels_config.aiwake.tools.validate_aiwake_captions import _closing, _disclosure_lines, _opening

    qa = row.get("caption_qa") if isinstance(row.get("caption_qa"), dict) else {}
    tiktok = str(((row.get("platform_overrides") or {}).get("tiktok") or {}).get("caption") or "")
    disclosures = _disclosure_lines(tiktok)
    history.note(
        angle=str(qa.get("angle") or ""),
        opening=_opening(tiktok),
        closing=_closing(tiktok),
        disclosure=disclosures[0] if disclosures else str(qa.get("disclosure_line") or ""),
        tags=list((row.get("base_metadata") or {}).get("hashtags") or []),
    )


def stamp_and_park(rows: list[dict[str, Any]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for row in rows:
        status, reason = stamp_production_status(row)
        counts[status] += 1
        if status != "ready":
            park_non_ready(row, status, reason)
    return counts


def field_diff(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> list[str]:
    """Paths that changed outside caption, status, and legacy fields."""
    before_by = {str(row.get("session_id") or ""): row for row in before}
    after_by = {str(row.get("session_id") or ""): row for row in after}
    problems: list[str] = []
    if set(before_by) != set(after_by):
        problems.append("session_id set changed")
        return problems
    for sid, old in before_by.items():
        new = after_by[sid]
        problems.extend(_diff_value(sid, old, new))
    return problems


def _diff_value(sid: str, old: Any, new: Any, path: str = "") -> list[str]:
    if isinstance(old, dict) and isinstance(new, dict):
        keys = set(old) | set(new)
        found: list[str] = []
        for key in sorted(keys):
            child = f"{path}.{key}" if path else str(key)
            if _allowed(child):
                continue
            found.extend(_diff_value(sid, old.get(key), new.get(key), child))
        return found
    if old != new:
        return [f"{sid}: {path}"]
    return []


def _allowed(path: str) -> bool:
    head = path.split(".", 1)[0]
    if head in _CAPTION_KEYS:
        return True
    if head == "base_metadata":
        return path.split(".")[-1] in _BASE_KEYS
    if head == "platform_overrides":
        parts = path.split(".")
        return len(parts) >= 3 and parts[-1] in _OVERRIDE_KEYS
    return False


def repair(rows: list[dict[str, Any]], *, library_path: Path | None = None) -> dict[str, Any]:
    """Generate captions for ready rows in posting order. Auth storms abort."""
    path = library_path or _LIBRARY
    ready = [row for row in posting_order(rows) if str(row.get("production_status") or "") == "ready"]
    expected = len(ready)
    history = CaptionHistory(expected=max(expected, 1))
    accepted: list[dict[str, Any]] = []
    stats: Counter[str] = Counter()
    models: Counter[str] = Counter()
    auth_streak = 0
    processed = 0
    for row in ready:
        sid = str(row.get("session_id") or "")
        if sid in SPEAKER_RELABELED_SESSIONS and not _already_ok(row):
            pack = generate_caption_pack(row, history=history, peers=accepted, expected_ready=expected)
            apply_caption_pack(row, pack)
            stats["needs_review"] += 1
            stats["speaker_relabeled_post_hoc"] += 1
            continue
        if _already_ok(row):
            _note_from_row(history, row)
            accepted.append(row)
            stats["resumed"] += 1
            models[str((row.get("caption_qa") or {}).get("model") or "")] += 1
            continue
        try:
            pack = generate_caption_pack(
                row,
                history=history,
                peers=list(accepted),
                expected_ready=expected,
            )
        except CaptionAuthError as exc:
            auth_streak += 1
            apply_caption_pack(
                row,
                {
                    "caption_qa": {
                        "status": "needs_review",
                        "reason": "auth_or_config",
                        "attempts": auth_streak,
                        "last_error": str(exc)[:500],
                        "generator": "captions_v3",
                    }
                },
            )
            stats["needs_review"] += 1
            if auth_streak >= 5:
                save_distribution_library(path, rows)
                raise CaptionRunAborted(str(exc)) from exc
            continue
        auth_streak = 0
        apply_caption_pack(row, pack)
        qa = pack.get("caption_qa") or {}
        status = str(qa.get("status") or "")
        if status == "ok":
            _note_from_row(history, row)
            accepted.append(row)
            stats["ok"] += 1
            models[str(qa.get("model") or "")] += 1
            attempts = int(qa.get("attempts") or 1)
            if attempts > 1:
                stats["retried"] += attempts - 1
        else:
            stats["needs_review"] += 1
            stats[str(qa.get("reason") or "needs_review")] += 1
        processed += 1
        if processed % 10 == 0:
            save_distribution_library(path, rows)
            print(f"checkpoint {processed} ok={stats['ok']} review={stats['needs_review']}", flush=True)
    save_distribution_library(path, rows)
    finalize_rotation(rows)
    save_distribution_library(path, rows)
    code, grouped = validate_library(rows)
    return {
        "stats": dict(stats),
        "models": dict(models),
        "validator_exit": code,
        "validator": {rule: len(items) for rule, items in grouped.items()},
        "ready": expected,
    }


def finalize_rotation(rows: list[dict[str, Any]]) -> None:
    """Assign angle letters and disclosure lines in posting order.

    The model writes the sentences. These two fields are bookkeeping so a
    legal rotation survives posts that land between ones already accepted.
    """
    from channels_config.aiwake.tools.caption_generator import _DISCLOSURE_BANK
    from channels_config.aiwake.tools.validate_aiwake_captions import _qa

    ok = [
        row for row in posting_order(rows)
        if _qa(row).get("status") == "ok" and _qa(row).get("generator") == "captions_v3"
    ]
    for index, row in enumerate(ok):
        line = _DISCLOSURE_BANK[index % len(_DISCLOSURE_BANK)]
        qa = row.get("caption_qa") if isinstance(row.get("caption_qa"), dict) else {}
        qa["angle"] = "ABCD"[index % 4]
        qa["disclosure_line"] = line
        row["caption_qa"] = qa

        def _swap(text: str, line: str = line) -> str:
            parts = [part.strip() for part in text.splitlines() if part.strip() and "unscripted" not in part.lower()]
            tags = [part for part in parts if part.startswith("#")]
            body = [part for part in parts if not part.startswith("#")]
            return "\n\n".join([*body, line, *tags]).strip()

        for key in (
            "final_caption",
            "humanized_caption",
            "post_planner_caption",
            "tiktok_caption",
            "facebook_caption",
            "linkedin_caption",
        ):
            if isinstance(row.get(key), str):
                row[key] = _swap(row[key])
        for name, block in (row.get("platform_overrides") or {}).items():
            if isinstance(block, dict) and isinstance(block.get("caption"), str):
                block["caption"] = _swap(block["caption"])
                if name == "x" and len(block["caption"]) > 280:
                    pieces = [
                        part for part in block["caption"].split("\n\n")
                        if part and not part.startswith("#") and "unscripted" not in part.lower()
                    ]
                    block["caption"] = "\n\n".join(pieces).strip()


def library_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    rows = load_distribution_library(_LIBRARY)
    backup = _backup(_LIBRARY)
    print(f"backup {backup}", flush=True)
    before = json.loads(backup.read_text(encoding="utf-8"))
    counts = stamp_and_park(rows)
    print(
        "production_status "
        + " ".join(f"{key}={counts[key]}" for key in ("ready", "incomplete", "rejected", "test")),
        flush=True,
    )
    scope = {
        "counts": dict(counts),
        "ready": [
            str(row.get("session_id") or "")
            for row in posting_order(rows)
            if row.get("production_status") == "ready"
        ],
        "incomplete": [
            str(row.get("session_id") or "")
            for row in rows
            if row.get("production_status") == "incomplete"
        ],
        "rejected": [
            str(row.get("session_id") or "")
            for row in rows
            if row.get("production_status") == "rejected"
        ],
    }
    _SCOPE.write_text(json.dumps(scope, indent=2) + "\n", encoding="utf-8")
    save_distribution_library(_LIBRARY, rows)
    try:
        result = repair(rows, library_path=_LIBRARY)
    except CaptionRunAborted as exc:
        print(f"aborted: {exc}", flush=True)
        return 2
    drifted = field_diff(before, rows)
    print(f"field_diff {len(drifted)}", flush=True)
    for item in drifted[:20]:
        print(f"  {item}", flush=True)
    print(json.dumps(result, indent=2), flush=True)
    return 0 if result["validator_exit"] == 0 and not drifted else 1


if __name__ == "__main__":
    raise SystemExit(main())
