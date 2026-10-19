"""Upload eligible Aiwake videos as private + publishAt. Resumable across quota days."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.posting.youtube_publisher import (  # noqa: E402
    build_youtube_client_for_page,
    list_channel_upload_videos,
    upload_short,
)
from channels_config.aiwake.tools.production_status import is_publishable  # noqa: E402
from channels_config.aiwake.tools.repair_captions import _backup  # noqa: E402
from channels_config.aiwake.tools.scope_qc_captions_v4 import align_rotation  # noqa: E402
from channels_config.aiwake.tools.validate_aiwake_captions import validate_library  # noqa: E402
from modules.distribution_contract import (  # noqa: E402
    load_distribution_library,
    save_distribution_library,
)

LIBRARY = ROOT / "channels_config" / "aiwake" / "store" / "content_library.json"
QUEUE = ROOT / "outputs" / "aiwake" / "youtube_upload_queue.json"
BRT = timezone(timedelta(hours=-3))
# First slot after the last existing one (08/10 19:00 BRT).
FIRST = datetime(2026, 10, 9, 12, 0, tzinfo=BRT)


def _slots(start: datetime, count: int) -> list[datetime]:
    cursor = start
    found = []
    while len(found) < count:
        if cursor.hour in {12, 19}:
            found.append(cursor)
        cursor += timedelta(hours=1)
    return found


def _quota(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(token in text for token in ("quota", "uploadlimit", "dailylimit", "daily upload limit", "ratelimit"))


def _eligible(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    picked = []
    for row in rows:
        if not is_publishable(row):
            continue
        yt = ((row.get("platform_overrides") or {}).get("youtube") or {})
        if str(yt.get("video_id") or "").strip():
            continue
        dist = ((row.get("distribution") or {}).get("youtube") or {})
        if str(dist.get("status") or "pending") != "pending":
            continue
        if not Path(str(row.get("video_path") or "")).is_file():
            continue
        picked.append(row)
    picked.sort(key=lambda row: str(row.get("session_id") or ""))
    return picked


def main() -> int:
    rows = load_distribution_library(LIBRARY)
    pending = _eligible(rows)
    state = json.loads(QUEUE.read_text(encoding="utf-8")) if QUEUE.is_file() else {}
    if state.get("quota_hit") and str(state.get("quota_day") or "") == datetime.now(timezone.utc).strftime("%Y-%m-%d"):
        print("quota already hit today; resume tomorrow", QUEUE)
        return 0
    done = set(state.get("uploaded") or [])
    slot_index = int(state.get("next_slot") or 0)
    pending = [row for row in pending if str(row.get("session_id") or "") not in done]
    youtube = build_youtube_client_for_page("aiwake", enforce_channel=True)
    uploads = list_channel_upload_videos(youtube, max_pages=20)
    existing_titles = {str(item.get("title") or "").strip().lower() for item in uploads}
    slots = _slots(FIRST, slot_index + len(pending) + 2)
    backup = _backup(LIBRARY)
    log_rows = []
    quota_hit = False
    for row in pending:
        title = str(((row.get("platform_overrides") or {}).get("youtube") or {}).get("title") or "")
        if title.strip().lower() in existing_titles:
            log_rows.append({"session_id": row.get("session_id"), "skipped": "title_on_channel"})
            continue
        slot = slots[slot_index]
        description = str(((row.get("platform_overrides") or {}).get("youtube") or {}).get("caption") or "")
        tags = [tag.lstrip("#") for tag in (row.get("base_metadata") or {}).get("hashtags") or []]
        try:
            video_id, _url, _when = upload_short(
                row["video_path"],
                title,
                description,
                tags=tags,
                privacy_status="private",
                publish_at=slot.astimezone(timezone.utc),
                category_id="28",
                page_name="aiwake",
                youtube=youtube,
                skip_playlist=True,
                preserve_title=True,
            )
        except Exception as exc:  # noqa: BLE001
            if _quota(exc):
                quota_hit = True
                log_rows.append({"session_id": row.get("session_id"), "quota": str(exc)[:240]})
                QUEUE.parent.mkdir(parents=True, exist_ok=True)
                QUEUE.write_text(
                    json.dumps(
                        {
                            "uploaded": sorted(done),
                            "next_slot": slot_index,
                            "quota_hit": True,
                            "quota_day": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                break
            log_rows.append({"session_id": row.get("session_id"), "error": str(exc)[:240]})
            continue
        listed = youtube.videos().list(part="snippet,status", id=video_id).execute()
        item = (listed.get("items") or [{}])[0]
        status = item.get("status") or {}
        yt = row.setdefault("platform_overrides", {}).setdefault("youtube", {})
        yt["video_id"] = video_id
        yt["scheduled_time"] = slot.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        yt["title"] = title
        posting = row.setdefault("posting_status", {})
        posting["youtube"] = "scheduled"
        dist = row.setdefault("distribution", {}).setdefault("youtube", {})
        dist["status"] = "scheduled"
        dist["scheduled_time_utc"] = yt["scheduled_time"]
        dist["post_id"] = video_id
        dist["via"] = "youtube"
        dist["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        done.add(str(row.get("session_id") or ""))
        slot_index += 1
        existing_titles.add(title.strip().lower())
        log_rows.append(
            {
                "session_id": row.get("session_id"),
                "video_id": video_id,
                "title": (item.get("snippet") or {}).get("title"),
                "privacy": status.get("privacyStatus"),
                "publishAt": status.get("publishAt") or "",
                "slot_brt": slot.strftime("%Y-%m-%d %H:%M"),
            }
        )
        save_distribution_library(LIBRARY, rows)
        QUEUE.parent.mkdir(parents=True, exist_ok=True)
        QUEUE.write_text(
            json.dumps(
                {
                    "uploaded": sorted(done),
                    "next_slot": slot_index,
                    "quota_hit": quota_hit,
                    "quota_day": datetime.now(timezone.utc).strftime("%Y-%m-%d") if quota_hit else "",
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    align_rotation(rows)
    code, _grouped = validate_library(rows)
    if code == 0:
        save_distribution_library(LIBRARY, rows)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log = ROOT / "outputs" / "aiwake" / f"youtube_apply_{stamp}.json"
    log.write_text(
        json.dumps(
            {
                "backup": str(backup),
                "eligible": len(pending),
                "uploaded_this_run": sum(1 for item in log_rows if item.get("video_id")),
                "quota_hit": quota_hit,
                "remaining_vs_200": 200 - len(done),
                "rows": log_rows,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(log)
    print("uploaded", sum(1 for item in log_rows if item.get("video_id")), "quota", quota_hit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
