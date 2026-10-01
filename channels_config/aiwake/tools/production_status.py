# -*- coding: utf-8 -*-
"""One production status for every Aiwake library row, and the publish gate.

Status comes from the same folder and filename rules as
``repair_captions.classify``:

- test: ``pipeline._PROTOTYPE_NAMES`` / ``_prototype``, or ``excluded_video_folder == "tests"``
- rejected: any other excluded folder, or not an animation clip
- incomplete: no target reply, audio under 8s, or the video file is missing
- ready: everything else

``is_publishable`` is the only skip check exporters should use.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from channels_config.aiwake.pipeline import _PROTOTYPE_NAMES
from channels_config.aiwake.tools.schedule_youtube import excluded_video_folder, is_animation_clip

# pipeline.py:208 _PROTOTYPE_NAMES; schedule_youtube.is_animation_clip / excluded_video_folder.
# Line numbers for the schedule helpers are filled by ``source_citation`` after import.
PRODUCTION_STATUS_VALUES = ("ready", "test", "rejected", "incomplete")

# HEAD `_tmp_rerevoice_illegal.py` rewrote orchestrator labels without a new completion.
SPEAKER_RELABELED_SESSIONS = frozenset({
    "20260928_215814_ce428c",
    "20260928_220547_711701",
})

_LIVE_TOP_LEVEL = (
    "final_caption",
    "humanized_caption",
    "post_planner_caption",
    "tiktok_caption",
    "facebook_caption",
    "linkedin_caption",
)
_OVERRIDE_TEXT_KEYS = ("caption", "title", "description")
_PLATFORM_NAMES = (
    "tiktok",
    "instagram",
    "facebook",
    "youtube",
    "x",
    "linkedin",
    "kwai",
    "pinterest",
)


def source_citation() -> str:
    """File:line for the rules ``derive_production_status`` actually calls."""
    return (
        "channels_config/aiwake/pipeline.py:208; "
        f"channels_config/aiwake/tools/schedule_youtube.py:{is_animation_clip.__code__.co_firstlineno}; "
        f"channels_config/aiwake/tools/schedule_youtube.py:{excluded_video_folder.__code__.co_firstlineno}; "
        "channels_config/aiwake/tools/caption_generator.py:_incomplete"
    )


def _prototype(path: str) -> bool:
    name = Path(path).name.lower()
    return (
        name in _PROTOTYPE_NAMES
        or name.startswith(("test_avatar_battle", "proof_", "spoken_test_", "synthetic_test_"))
        or "_test_render_battle" in name
        or "_render." in name
        or "_render_" in name
        or "test_archive" in {part.lower() for part in Path(path).parts}
    )


def derive_production_status(row: dict[str, Any]) -> tuple[str, str]:
    """Return ``(status, reason)`` using the repo's folder and transcript rules."""
    from channels_config.aiwake.tools.caption_generator import _incomplete, _read_turns

    path = str((row or {}).get("video_path") or "")
    folder = excluded_video_folder(path)
    if _prototype(path) or folder == "tests":
        why = "prototype filename" if _prototype(path) else "tests folder"
        return "test", why
    if folder:
        return "rejected", f"excluded folder {folder}"
    if path and not is_animation_clip(path):
        return "rejected", "not an animation clip"
    if not path or not Path(path).is_file():
        return "incomplete", "video file is missing on disk"
    reason = _incomplete(_read_turns(row))
    if reason:
        return "incomplete", reason
    return "ready", "animation clip with a target reply"


def stamp_production_status(row: dict[str, Any]) -> tuple[str, str]:
    """Write ``production_status*`` onto ``row`` and return the pair."""
    status, reason = derive_production_status(row)
    row["production_status"] = status
    row["production_status_reason"] = reason
    row["production_status_source"] = source_citation()
    return status, reason


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def snapshot_live_captions(row: dict[str, Any]) -> dict[str, Any]:
    """Copy the live caption fields. Does not remove them."""
    snap: dict[str, Any] = {}
    for key in _LIVE_TOP_LEVEL:
        if key in row:
            snap[key] = row.get(key)
    base = row.get("base_metadata") if isinstance(row.get("base_metadata"), dict) else {}
    snap["base_metadata"] = {
        "title": base.get("title", ""),
        "caption": base.get("caption", ""),
        "hashtags": list(base.get("hashtags") or []) if isinstance(base.get("hashtags"), list) else base.get("hashtags"),
    }
    overrides = row.get("platform_overrides") if isinstance(row.get("platform_overrides"), dict) else {}
    stored: dict[str, Any] = {}
    for name in _PLATFORM_NAMES:
        block = overrides.get(name)
        if not isinstance(block, dict):
            continue
        stored[name] = {key: block.get(key) for key in _OVERRIDE_TEXT_KEYS if key in block}
    snap["platform_overrides"] = stored
    return snap


def clear_live_captions(row: dict[str, Any]) -> None:
    """Blank live caption fields. Identity fields stay, including YouTube id and time."""
    for key in _LIVE_TOP_LEVEL:
        if key in row:
            row[key] = ""
    base = row.get("base_metadata")
    if isinstance(base, dict):
        if "title" in base:
            base["title"] = ""
        if "caption" in base:
            base["caption"] = ""
        if "hashtags" in base:
            base["hashtags"] = []
    overrides = row.get("platform_overrides")
    if isinstance(overrides, dict):
        for name in _PLATFORM_NAMES:
            block = overrides.get(name)
            if not isinstance(block, dict):
                continue
            for key in _OVERRIDE_TEXT_KEYS:
                if key in block:
                    block[key] = ""


def park_non_ready(row: dict[str, Any], status: str, reason: str) -> None:
    """Move live captions into ``legacy_captions`` and blank the live fields.

    A second call keeps the original ``legacy_captions`` snapshot.
    """
    if not isinstance(row.get("legacy_captions"), dict):
        row["legacy_captions"] = snapshot_live_captions(row)
    clear_live_captions(row)
    row["caption_qa"] = {
        "status": f"blocked_{status}",
        "reason": reason,
        "checked_at": _now(),
    }


def stored_status(row: dict[str, Any]) -> str:
    status = str((row or {}).get("production_status") or "").strip().lower()
    if status in PRODUCTION_STATUS_VALUES:
        return status
    derived, _reason = derive_production_status(row or {})
    return derived


def is_publishable(row: dict[str, Any] | None) -> bool:
    """True only for an in-scope ready row that passed QC and captions v4."""
    if not isinstance(row, dict):
        return False
    if stored_status(row) != "ready":
        return False
    if str(row.get("production_scope") or "") != "in":
        return False
    review = row.get("quality_review") if isinstance(row.get("quality_review"), dict) else {}
    if str(review.get("status") or "") != "pass":
        return False
    qa = row.get("caption_qa") if isinstance(row.get("caption_qa"), dict) else {}
    if str(qa.get("status") or "") != "ok":
        return False
    if str(qa.get("generator") or "") != "captions_v4":
        return False
    if not str(qa.get("model") or "").strip():
        return False
    from channels_config.aiwake.tools.validate_aiwake_captions import entry_failures

    return not entry_failures(row)


def distribution_status(row: dict[str, Any], platform: str) -> str:
    """Posting status for one platform. ``distribution`` wins when it is set."""
    dist = row.get("distribution") if isinstance(row.get("distribution"), dict) else {}
    block = dist.get(platform) if isinstance(dist, dict) else None
    if isinstance(block, dict) and str(block.get("status") or "").strip():
        return str(block.get("status") or "").strip().lower()
    posting = row.get("posting_status") if isinstance(row.get("posting_status"), dict) else {}
    return str(posting.get(platform) or "pending").strip().lower() or "pending"


def scheduled_time_of(row: dict[str, Any]) -> str:
    youtube = ((row.get("platform_overrides") or {}).get("youtube") or {})
    if not isinstance(youtube, dict):
        return ""
    return str(youtube.get("scheduled_time") or "").strip()


def posting_order(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """YouTube ``scheduled_time`` ascending, then Post Planner recency, then session id.

    Post Planner sorts ``_row_sort_key`` newest-first (``post_planner.py``).
    """
    from channels_config.aiwake.tools.post_planner import _row_sort_key

    ranked = sorted(rows, key=_row_sort_key, reverse=True)
    rank = {str(row.get("session_id") or ""): index for index, row in enumerate(ranked)}

    def _key(row: dict[str, Any]) -> tuple:
        when = scheduled_time_of(row)
        sid = str(row.get("session_id") or "")
        return (0 if when else 1, when or "", rank.get(sid, 10**9), sid)

    return sorted(rows, key=_key)
