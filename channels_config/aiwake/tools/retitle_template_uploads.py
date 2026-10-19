"""Replace "What should a viewer ask" titles with the shortened opening question.

Updates YouTube snippet titles only. Privacy and publishAt are left as they are.
Never sets a video public. Library rows without a video id are retitled locally.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.posting.youtube_publisher import (  # noqa: E402
    build_youtube_client_for_page,
    list_channel_upload_videos,
)
from channels_config.aiwake.tools.repair_captions import _backup  # noqa: E402
from channels_config.aiwake.tools.scope_qc_captions_v4 import _shorten_question  # noqa: E402
from modules.distribution_contract import load_distribution_library, save_distribution_library  # noqa: E402

LIBRARY = ROOT / "channels_config" / "aiwake" / "store" / "content_library.json"
OUT = ROOT / "outputs" / "aiwake"
NEEDLE = "what should a viewer ask"


def _title_of(row: dict[str, Any]) -> str:
    yt = ((row.get("platform_overrides") or {}).get("youtube") or {})
    return str(yt.get("title") or (row.get("base_metadata") or {}).get("title") or "")


def _opening(row: dict[str, Any]) -> str:
    for item in row.get("spoken_utterances") or []:
        if isinstance(item, dict) and item.get("role") == "orchestrator" and item.get("text"):
            return str(item.get("text") or "")
    return ""


def new_title(old: str, opening: str, used: set[str]) -> str:
    prefix = old.split(" - ", 1)[0].strip() + " - "
    title = prefix + _shorten_question(opening, limit=80 - len(prefix))
    if title.lower() in used:
        title = title[:-1] + " now?"
    if len(title) > 80:
        raise ValueError(f"title still over 80: {title}")
    if NEEDLE in title.lower():
        raise ValueError(f"template title survived: {title}")
    used.add(title.lower())
    return title


def _replace_headline(text: str, old: str, new: str) -> str:
    if not text or not old:
        return text
    lines = text.splitlines()
    if lines and lines[0].strip() == old.strip():
        lines[0] = new
        return "\n".join(lines)
    return text


def plan(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    used = {
        _title_of(row).lower()
        for row in rows
        if NEEDLE not in _title_of(row).lower() and _title_of(row)
    }
    planned = []
    for row in rows:
        old = _title_of(row)
        if NEEDLE not in old.lower():
            continue
        opening = _opening(row)
        if not opening:
            raise SystemExit(f"no opening for {row.get('session_id')}")
        yt = (row.get("platform_overrides") or {}).get("youtube") or {}
        planned.append(
            {
                "session_id": str(row.get("session_id") or ""),
                "video_id": str(yt.get("video_id") or ""),
                "old": old,
                "new": new_title(old, opening, used),
            }
        )
    return planned


def _chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def _list_videos(youtube, ids: list[str]) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for chunk in _chunks(ids, 50):
        response = youtube.videos().list(part="snippet,status", id=",".join(chunk)).execute()
        for item in response.get("items") or []:
            found[item["id"]] = item
    return found


def _apply_youtube(youtube, planned: list[dict[str, Any]]) -> list[dict[str, Any]]:
    uploaded = [item for item in planned if item["video_id"]]
    live = _list_videos(youtube, [item["video_id"] for item in uploaded])
    applied = []
    for item in uploaded:
        vid = item["video_id"]
        current = live.get(vid)
        if current is None:
            applied.append({**item, "error": "missing_from_videos_list"})
            continue
        snippet = dict(current.get("snippet") or {})
        body_snippet = {
            "title": item["new"],
            "description": snippet.get("description") or "",
            "categoryId": snippet.get("categoryId") or "28",
        }
        if snippet.get("tags"):
            body_snippet["tags"] = snippet["tags"]
        if snippet.get("defaultLanguage"):
            body_snippet["defaultLanguage"] = snippet["defaultLanguage"]
        if snippet.get("defaultAudioLanguage"):
            body_snippet["defaultAudioLanguage"] = snippet["defaultAudioLanguage"]
        privacy_before = ((current.get("status") or {}).get("privacyStatus") or "")
        youtube.videos().update(part="snippet", body={"id": vid, "snippet": body_snippet}).execute()
        applied.append({**item, "privacy_before": privacy_before, "categoryId": body_snippet["categoryId"]})
    return applied


def _replace_tree(value: Any, old: str, new: str) -> Any:
    if isinstance(value, dict):
        return {key: _replace_tree(item, old, new) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_tree(item, old, new) for item in value]
    if isinstance(value, str) and (value.strip() == old.strip() or value.splitlines()[:1] == [old]):
        return _replace_headline(value, old, new)
    return value


def _write_library(rows: list[dict[str, Any]], planned: list[dict[str, Any]]) -> None:
    by_session = {item["session_id"]: item for item in planned}
    for index, row in enumerate(rows):
        item = by_session.get(str(row.get("session_id") or ""))
        if item is None:
            continue
        rows[index] = _replace_tree(row, item["old"], item["new"])


def _library_from_plan(plan_path: Path) -> int:
    planned = json.loads(plan_path.read_text(encoding="utf-8"))
    backup = _backup(LIBRARY)
    rows = load_distribution_library(LIBRARY)
    _write_library(rows, planned)
    save_distribution_library(LIBRARY, rows)
    left = sum(1 for row in rows if NEEDLE in json.dumps(row).lower())
    print(f"backup {backup.name} library_template_titles {left}")
    return 1 if left else 0


def main() -> int:
    if "--library-plan" in sys.argv:
        plan_path = Path(sys.argv[sys.argv.index("--library-plan") + 1])
        return _library_from_plan(plan_path)
    apply = "--apply" in sys.argv
    rows = load_distribution_library(LIBRARY)
    planned = plan(rows)
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    preview = OUT / f"retitle_plan_{stamp}.json"
    preview.write_text(json.dumps(planned, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"planned {len(planned)} uploaded {sum(1 for item in planned if item['video_id'])} -> {preview}")
    if not apply:
        return 0

    youtube = build_youtube_client_for_page("aiwake", enforce_channel=True)
    applied = _apply_youtube(youtube, planned)
    ids = [item["video_id"] for item in applied if item.get("video_id") and not item.get("error")]
    readback = _list_videos(youtube, ids)
    mismatches = []
    for item in applied:
        vid = item.get("video_id") or ""
        if not vid or item.get("error"):
            continue
        live = readback.get(vid) or {}
        title = ((live.get("snippet") or {}).get("title") or "")
        privacy = ((live.get("status") or {}).get("privacyStatus") or "")
        item["readback_title"] = title
        item["readback_privacy"] = privacy
        item["publishAt"] = (live.get("status") or {}).get("publishAt") or ""
        if title != item["new"]:
            mismatches.append(vid)
        if privacy == "public" and item.get("privacy_before") != "public":
            mismatches.append(f"{vid}:became_public")
    uploads = list_channel_upload_videos(youtube, max_pages=20)
    still = [row.get("video_id") for row in uploads if NEEDLE in str(row.get("title") or "").lower()]
    backup = _backup(LIBRARY)
    fresh = load_distribution_library(LIBRARY)
    succeeded = {
        item["session_id"]
        for item in applied
        if item.get("session_id") and not item.get("error") and item.get("readback_title") == item.get("new")
    }
    local_only = {item["session_id"] for item in planned if not item["video_id"]}
    _write_library(fresh, [item for item in planned if item["session_id"] in succeeded or item["session_id"] in local_only])
    save_distribution_library(LIBRARY, fresh)
    log = {
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "backup": backup.name,
        "applied": applied,
        "mismatches": mismatches,
        "template_titles_still_on_youtube": still,
        "library_template_titles": sum(1 for row in fresh if NEEDLE in json.dumps(row).lower()),
    }
    path = OUT / f"retitle_apply_{stamp}.json"
    path.write_text(json.dumps(log, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"mismatches {len(mismatches)} youtube_left {len(still)} library_left {log['library_template_titles']} -> {path}")
    return 1 if mismatches or still or log["library_template_titles"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
