# -*- coding: utf-8 -*-
"""Dry-run, then patch YouTube snippets for future private uploads.

Default mode lists the real video state and writes a dry-run JSON. ``--apply``
refuses to run unless that JSON was produced from the same library bytes.

``videos().update`` is called with ``part="snippet"`` only. Privacy and
``publishAt`` are never sent.

    python -m channels_config.aiwake.tools.update_youtube_scheduled
    python -m channels_config.aiwake.tools.update_youtube_scheduled --apply --dry-run-json PATH
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

if __package__ in (None, ""):  # pragma: no cover
    _FACTORY = Path(__file__).resolve().parents[3]
    if str(_FACTORY) not in sys.path:
        sys.path.insert(0, str(_FACTORY))

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[3] / ".env", override=False)
except ImportError:
    pass

from modules.distribution_contract import (
    content_library_path,
    load_distribution_library,
    save_distribution_library,
)
from modules.durable_store import restore_channel_state

from channels_config.aiwake.tools.production_status import is_publishable
from channels_config.aiwake.tools.schedule_youtube import map_youtube_payload
from channels_config.aiwake.tools.sync_youtube_metadata import CHANNEL_ID
from channels_config.aiwake.tools.validate_aiwake_captions import require_valid_library

_LOG = logging.getLogger("aiwake.update_scheduled")
_BRT = ZoneInfo("America/Sao_Paulo")
_PAST_PRIVATE_IDS = (
    "JUvSRpYoKXE",
    "WlUYcFTNUFQ",
    "pnjGaf7qI8M",
    "Rh8nGUNxI94",
    "xAoOEyn0MFI",
)
_QUOTA_UNITS = 50


def _youtube_video_id(row: dict[str, Any]) -> str:
    youtube = ((row.get("platform_overrides") or {}).get("youtube") or {})
    if not isinstance(youtube, dict):
        youtube = {}
    return str(youtube.get("video_id") or row.get("youtube_video_id") or "").strip()


def _parse_when(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _brt(value: str) -> str:
    parsed = _parse_when(value)
    if parsed is None:
        return ""
    return parsed.astimezone(_BRT).strftime("%Y-%m-%d %H:%M %Z")


def _normalize(video: dict[str, Any]) -> dict[str, Any]:
    status = video.get("status") if isinstance(video.get("status"), dict) else {}
    snippet = video.get("snippet") if isinstance(video.get("snippet"), dict) else {}
    privacy = str(status.get("privacyStatus") or video.get("privacy") or "").strip().lower()
    publish_at = str(status.get("publishAt") or video.get("publish_at") or video.get("publishAt") or "")
    return {
        "id": str(video.get("id") or video.get("video_id") or "").strip(),
        "privacy": privacy,
        "publish_at": publish_at,
        "snippet": snippet,
        "status": status,
        "title": str(snippet.get("title") or video.get("title") or ""),
        "description": str(snippet.get("description") or video.get("description") or ""),
    }


def _future_private(video: dict[str, Any], *, now: datetime | None = None) -> bool:
    if video["privacy"] != "private":
        return False
    when = _parse_when(video["publish_at"])
    if when is None:
        return False
    current = now or datetime.now(timezone.utc)
    return when > current


def _status_from_api(video: dict[str, Any], *, now: datetime | None = None) -> str:
    if video["privacy"] == "public":
        return "posted"
    if _future_private(video, now=now):
        return "scheduled"
    if video["privacy"] == "private":
        return "private"
    return video["privacy"] or "unknown"


def _tags_for(row: dict[str, Any]) -> list[str]:
    payload = map_youtube_payload(row)
    tags = [str(tag).lstrip("#") for tag in payload.get("tags") or []]
    base = row.get("base_metadata") if isinstance(row.get("base_metadata"), dict) else {}
    for tag in base.get("hashtags") or []:
        cleaned = str(tag).lstrip("#").strip()
        if cleaned and cleaned not in tags:
            tags.append(cleaned)
    return tags


def _snippet_body(video: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    payload = map_youtube_payload(row)
    current = video.get("snippet") or {}
    snippet: dict[str, Any] = {
        "title": str(payload.get("title") or ""),
        "description": str(payload.get("description") or ""),
        "tags": _tags_for(row),
        "categoryId": str(current.get("categoryId") or payload.get("category_id") or "28"),
    }
    for key in ("defaultLanguage", "defaultAudioLanguage"):
        if current.get(key):
            snippet[key] = current[key]
    return snippet


def _library_sha(rows: list[dict[str, Any]]) -> str:
    raw = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def sync_posting_status(rows: list[dict[str, Any]], videos: list[dict[str, Any]]) -> list[str]:
    """Set ``posting_status.youtube`` from API privacy. Never touch ``scheduled_time``."""
    by_id = {video["id"]: video for video in videos if video.get("id")}
    changes: list[str] = []
    for row in rows:
        video_id = _youtube_video_id(row)
        video = by_id.get(video_id)
        if video is None:
            continue
        wanted = _status_from_api(video)
        if wanted == "unknown":
            continue
        status = row.get("posting_status")
        if not isinstance(status, dict):
            status = {}
            row["posting_status"] = status
        current = str(status.get("youtube") or "")
        if current == wanted:
            continue
        status["youtube"] = wanted
        changes.append(f"{row.get('session_id')} {video_id}: {current or '(empty)'} -> {wanted}")
    return changes


def update_scheduled(
    rows: list[dict[str, Any]],
    list_fn=None,
    update_fn=None,
    *,
    apply: bool = False,
    dryrun_path: Path | None = None,
    outputs_dir: Path | None = None,
    now: datetime | None = None,
    library_path: Path | None = None,
) -> dict[str, Any]:
    """List every library video. Apply snippet updates only for future private uploads."""
    require_valid_library(rows)
    ids = [vid for vid in (_youtube_video_id(row) for row in rows) if vid]
    if list_fn is None or update_fn is None:
        from agents.posting.youtube_publisher import build_youtube_client_for_page

        youtube = build_youtube_client_for_page(CHANNEL_ID, enforce_channel=True)
        if list_fn is None:
            list_fn = lambda wanted, _yt=youtube: _list_by_id(_yt, wanted)
        if update_fn is None:
            update_fn = lambda video_id, snippet, _yt=youtube: _update_snippet(_yt, video_id, snippet)

    listed = [_normalize(item) for item in (list_fn(ids) or []) if isinstance(item, dict)]
    by_id = {item["id"]: item for item in listed if item.get("id")}
    by_video = {_youtube_video_id(row): row for row in rows if _youtube_video_id(row)}
    actions: list[dict[str, Any]] = []
    for video_id, row in by_video.items():
        video = by_id.get(video_id)
        actions.append(_plan_row(row, video, apply=apply, now=now))
    for video_id, video in by_id.items():
        if video_id not in by_video:
            actions.append({
                "session_id": "",
                "video_id": video_id,
                "privacy": video["privacy"],
                "publish_at_brt": _brt(video["publish_at"]),
                "action": "SKIP: video id is not in the library",
            })

    status_changes = sync_posting_status(rows, listed)
    sha = _library_sha(rows)
    if library_path is not None and status_changes:
        save_distribution_library(library_path, rows)
    past_private = _past_private_section(rows, by_id, now=now)
    report = {
        "library_sha256": sha,
        "apply": bool(apply),
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "actions": actions,
        "status_sync": status_changes,
        "past_private": past_private,
        "quota_units_per_update": _QUOTA_UNITS,
    }
    dest = _write_dryrun(report, outputs_dir=outputs_dir)
    report["dryrun_path"] = str(dest)
    _print_table(actions)
    print("past private (scheduled_time already passed):")
    for item in past_private:
        print(
            f"  {item['slug']} {item['video_id']} privacy={item['privacy']} "
            f"publishAt={item['publish_at_brt'] or item['publish_at']} "
            f"ever_public={item['ever_public']} library={item['library_status']}"
        )
    for change in status_changes:
        print(f"status sync {change}")

    if not apply:
        return report

    if not _dryrun_matches(dryrun_path, sha):
        raise SystemExit("refusing --apply: dry-run JSON is missing or its library sha256 does not match")

    updated: list[str] = []
    for action in actions:
        if not str(action.get("action") or "").startswith("UPDATE"):
            continue
        video = by_id[action["video_id"]]
        row = by_video[action["video_id"]]
        snippet = _snippet_body(video, row)
        _LOG.info("snippet update %s quota=%s title=%s", action["video_id"], _QUOTA_UNITS, snippet.get("title"))
        update_fn(action["video_id"], snippet)
        updated.append(action["video_id"])
    report["updated"] = updated
    after = [_normalize(item) for item in (list_fn(updated) or []) if isinstance(item, dict)]
    after_by = {item["id"]: item for item in after}
    privacy_ok = True
    for video_id in updated:
        before = by_id[video_id]
        later = after_by.get(video_id)
        same = (
            later is not None
            and later["privacy"] == before["privacy"]
            and later["publish_at"] == before["publish_at"]
        )
        report.setdefault("privacy_check", []).append({
            "video_id": video_id,
            "before_privacy": before["privacy"],
            "after_privacy": None if later is None else later["privacy"],
            "before_publish_at": before["publish_at"],
            "after_publish_at": None if later is None else later["publish_at"],
            "unchanged": same,
        })
        privacy_ok = privacy_ok and same
        print(f"privacy check {video_id} unchanged={same}")
    if not privacy_ok:
        raise RuntimeError("privacyStatus or publishAt changed after a snippet update")
    if library_path is not None and status_changes:
        save_distribution_library(library_path, rows)
    return report


def _plan_row(row: dict[str, Any], video: dict[str, Any] | None, *, apply: bool, now: datetime | None) -> dict[str, Any]:
    video_id = _youtube_video_id(row)
    payload = map_youtube_payload(row)
    new_title = str(payload.get("title") or "")
    new_desc = str(payload.get("description") or "")
    base = {
        "session_id": str(row.get("session_id") or ""),
        "video_id": video_id,
        "library_status": str(((row.get("posting_status") or {}).get("youtube") or "")),
        "production_status": str(row.get("production_status") or ""),
        "caption_qa": str(((row.get("caption_qa") or {}).get("status") or "")),
        "new_title": new_title,
    }
    if video is None:
        return {**base, "privacy": "", "publish_at_brt": "", "current_title": "", "description_changed": False, "action": "SKIP: video id was not returned by the API"}
    changed = new_desc.strip() != str(video.get("description") or "").strip()
    record = {
        **base,
        "privacy": video["privacy"],
        "publish_at": video["publish_at"],
        "publish_at_brt": _brt(video["publish_at"]),
        "current_title": video["title"],
        "description_changed": changed,
    }
    if not is_publishable(row):
        record["action"] = "SKIP: not publishable"
        return record
    if not _future_private(video, now=now):
        why = "privacy is not private with a future publishAt"
        if video_id in _PAST_PRIVATE_IDS:
            why = "private with a publishAt already in the past"
        record["action"] = f"SKIP: {why}"
        return record
    record["action"] = "UPDATE" if apply else "UPDATE"
    return record


def _past_private_section(rows: list[dict[str, Any]], by_id: dict[str, dict[str, Any]], *, now: datetime | None) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for row in rows:
        video_id = _youtube_video_id(row)
        if video_id not in _PAST_PRIVATE_IDS:
            continue
        video = by_id.get(video_id) or {}
        privacy = str(video.get("privacy") or "")
        publish_at = str(video.get("publish_at") or "")
        found.append({
            "slug": str(row.get("session_id") or ""),
            "video_id": video_id,
            "privacy": privacy,
            "publish_at": publish_at,
            "publish_at_brt": _brt(publish_at),
            "ever_public": privacy == "public",
            "library_status": str(((row.get("posting_status") or {}).get("youtube") or "")),
            "library_note": (
                "No writer in this repo sets posting_status.youtube to private. "
                "persist_scheduled sets scheduled after a private+publishAt upload. "
                "Both 2026-09-29 backups already stored private for this video id."
            ),
            "future": _future_private(video, now=now) if video else False,
        })
    return found


def _write_dryrun(report: dict[str, Any], *, outputs_dir: Path | None) -> Path:
    root = outputs_dir or Path("outputs/aiwake")
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = root / f"youtube_update_dryrun_{stamp}.json"
    dest.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return dest


def _dryrun_matches(path: Path | None, sha: str) -> bool:
    if path is None or not path.is_file():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return str(payload.get("library_sha256") or "") == sha


def _print_table(actions: list[dict[str, Any]]) -> None:
    print(
        "session_id | video_id | privacy | publishAt BRT | library | production | qa | title | desc | action"
    )
    for item in actions:
        title = f"{item.get('current_title', '')} -> {item.get('new_title', '')}"
        print(
            f"{item.get('session_id','')} | {item.get('video_id','')} | {item.get('privacy','')} | "
            f"{item.get('publish_at_brt','')} | {item.get('library_status','')} | "
            f"{item.get('production_status','')} | {item.get('caption_qa','')} | {title} | "
            f"{'y' if item.get('description_changed') else 'n'} | {item.get('action','')}"
        )


def _list_by_id(youtube: Any, video_ids: list[str]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for start in range(0, len(video_ids), 50):
        chunk = video_ids[start : start + 50]
        response = youtube.videos().list(part="snippet,status", id=",".join(chunk)).execute()
        found.extend(response.get("items") or [])
    return found


def _update_snippet(youtube: Any, video_id: str, snippet: dict[str, Any]) -> str:
    body = {"id": video_id, "snippet": snippet}
    youtube.videos().update(part="snippet", body=body).execute()
    return video_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aiwake-update-youtube-scheduled")
    parser.add_argument("--library", type=Path)
    parser.add_argument("--apply", action="store_true", help="Patch snippets. Requires a matching dry-run JSON.")
    parser.add_argument("--dry-run-json", type=Path)
    parser.add_argument("--outputs-dir", type=Path)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.library is None:
        restore_channel_state(CHANNEL_ID)
    path = args.library or content_library_path(CHANNEL_ID)
    rows = load_distribution_library(path)
    update_scheduled(
        rows,
        apply=args.apply,
        dryrun_path=args.dry_run_json,
        outputs_dir=args.outputs_dir,
        library_path=path,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
