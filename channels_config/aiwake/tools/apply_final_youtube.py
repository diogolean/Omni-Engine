# -*- coding: utf-8 -*-
"""Apply SEO captions, then schedule Aiwake on YouTube. Never sets a video public."""
from __future__ import annotations

import hashlib
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from googleapiclient.errors import HttpError

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.posting.youtube_publisher import (  # noqa: E402
    build_youtube_client_for_page,
    list_channel_upload_videos,
    update_video_publish_at,
    upload_short,
)
from channels_config.aiwake.tools.caption_generator import is_complete_question  # noqa: E402
from channels_config.aiwake.tools.final_schedule import BRT, build_plan  # noqa: E402
from channels_config.aiwake.tools.repair_captions import _backup  # noqa: E402
from channels_config.aiwake.tools.seo_caption import rewrite_ready_rows, who_funds_template  # noqa: E402
from channels_config.aiwake.tools.validate_aiwake_captions import validate_library  # noqa: E402
from modules.distribution_contract import load_distribution_library, save_distribution_library  # noqa: E402

LIBRARY = ROOT / "channels_config" / "aiwake" / "store" / "content_library.json"
LEDGER_IN = Path(r"C:\Users\Freedom or Death\aiwake_stage_20260929\v8\publication_ledger.json")
TWENTY_IN = Path(r"C:\Users\Freedom or Death\aiwake_stage_20260929\v8\youtube20_20261004.json")
OUT = ROOT / "outputs" / "aiwake"
CHANNEL = "Aiwake"
CHANNEL_ID = "UC-gXZHj834QmClg95R43E5A"


def _md5(path: str) -> str:
    file = Path(path)
    if not file.is_file():
        return ""
    digest = hashlib.md5()
    with file.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pubs(row: dict[str, Any]) -> list[dict[str, Any]]:
    found = row.get("publications")
    if not isinstance(found, list):
        found = []
        row["publications"] = found
    return found


def _upsert(row: dict[str, Any], entry: dict[str, Any]) -> None:
    pubs = _pubs(row)
    for item in pubs:
        same_video = entry.get("video_id") and item.get("video_id") == entry.get("video_id") and item.get("platform") == entry.get("platform")
        same_file = entry.get("file_md5") and item.get("file_md5") == entry.get("file_md5") and item.get("platform") == entry.get("platform")
        if same_video or same_file:
            item.update(entry)
            return
    if entry.get("file_md5") and any(
        item.get("platform") == entry.get("platform") and item.get("file_md5") == entry.get("file_md5") for item in pubs
    ):
        return
    pubs.append(entry)


def _youtube_md5s(rows: list[dict[str, Any]]) -> set[str]:
    found = set()
    for row in rows:
        for item in row.get("publications") or []:
            if item.get("platform") == "youtube" and item.get("file_md5"):
                found.add(str(item["file_md5"]))
    return found


def _load_snippets(youtube, video_ids: list[str]) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    unique = list(dict.fromkeys(video_ids))
    for start in range(0, len(unique), 50):
        listed = youtube.videos().list(part="snippet,status", id=",".join(unique[start : start + 50])).execute()
        for item in listed.get("items") or []:
            found[item["id"]] = item
    return found


def _snippet_body(title: str, description: str, snippet: dict[str, Any]) -> dict[str, Any]:
    body = {
        "title": title[:100],
        "description": description[:4900],
        "categoryId": snippet.get("categoryId") or "28",
    }
    for key in ("tags", "defaultLanguage", "defaultAudioLanguage"):
        if snippet.get(key):
            body[key] = snippet[key]
    return body


def _snippet(youtube, video_id: str, title: str, description: str, snippet: dict[str, Any]) -> None:
    youtube.videos().update(
        part="snippet",
        body={"id": video_id, "snippet": _snippet_body(title, description, snippet)},
    ).execute()


def _is_quota(exc: BaseException) -> bool:
    return "quotaExceeded" in str(exc)


def _same_clock(left: str, right: str) -> bool:
    return (left or "").replace(".000Z", "Z")[:19] == (right or "").replace(".000Z", "Z")[:19]


def _known_md5(row: dict[str, Any]) -> str:
    for item in row.get("publications") or []:
        if item.get("platform") == "youtube" and item.get("file_md5"):
            return str(item["file_md5"])
    return _md5(str(row.get("video_path") or ""))


def _matches(item: dict[str, Any], title: str, description: str, publish_at: str) -> bool:
    snippet = item.get("snippet") or {}
    status = item.get("status") or {}
    if (snippet.get("title") or "") != title:
        return False
    if (snippet.get("description") or "").replace("\r\n", "\n") != description.replace("\r\n", "\n"):
        return False
    if not publish_at:
        return True
    return _same_clock(str(status.get("publishAt") or ""), publish_at)


def _push(
    youtube,
    video_id: str,
    title: str,
    description: str,
    snippet: dict[str, Any],
    publish_at: str,
    *,
    keep_privacy: bool,
) -> None:
    """One videos.update. Status is included only to schedule a private video."""
    body: dict[str, Any] = {"id": video_id, "snippet": _snippet_body(title, description, snippet)}
    part = "snippet"
    if publish_at and not keep_privacy:
        part = "snippet,status"
        body["status"] = {
            "privacyStatus": "private",
            "publishAt": publish_at,
            "selfDeclaredMadeForKids": False,
        }
    youtube.videos().update(part=part, body=body).execute()


def _row_result(item: dict[str, Any], **extra: Any) -> dict[str, Any]:
    kept = {
        "session_id": item.get("session_id") or "",
        "video_id": item.get("video_id") or "",
        "url": item.get("url") or "",
        "action": extra.get("action") or item.get("action") or "",
        "slot_brt": item.get("slot_brt") or "",
        "title": item.get("title") or "",
    }
    if extra.get("error"):
        kept["error"] = extra["error"]
    return kept


def _write_report(results: list[dict[str, Any]], note: str) -> None:
    samples = []
    sample_path = OUT / "final_before_after.json"
    if sample_path.is_file():
        samples = json.loads(sample_path.read_text(encoding="utf-8"))
    lines = [
        "# Aiwake YouTube schedule 2026-10-05",
        "",
        note,
        "",
        f"Scheduled rows in this run: {len(results)}.",
        "",
        "| BRT | video_id | URL | title |",
        "| --- | --- | --- | --- |",
    ]
    for item in results:
        if item.get("action") not in {"reschedule", "upload"}:
            continue
        url = item.get("url") or ""
        lines.append(
            f"| {item.get('slot_brt') or ''} | {item.get('video_id') or ''} | {url} | {item.get('title') or ''} |"
        )
    failed = [item for item in results if item.get("action") in {"upload_failed", "update_failed", "skip_duplicate_md5", "quota"}]
    if failed:
        lines.extend(["", "## Not scheduled", ""])
        for item in failed:
            lines.append(
                f"- {item.get('slot_brt') or ''} {item.get('action')} {item.get('session_id') or ''} "
                f"{item.get('video_id') or ''} {item.get('error') or ''}"
            )
    lines.extend(["", "## Caption samples", ""])
    for item in samples[:10]:
        lines.append(f"### {item.get('session_id')}")
        lines.append("")
        lines.append(f"Before title: {item.get('before_title')}")
        lines.append("")
        lines.append(f"After title: {item.get('after_title')}")
        lines.append("")
        lines.append("Before description:")
        lines.append("")
        lines.append(str(item.get("before_caption") or ""))
        lines.append("")
        lines.append("After description:")
        lines.append("")
        lines.append(str(item.get("after_caption") or ""))
        lines.append("")
    path = OUT / "report_final_20261005.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    print("report", path)


def resume() -> int:
    """Push titles and schedule videos. Do not rewrite captions. Never set a video public."""
    rows = load_distribution_library(LIBRARY)
    code, grouped = validate_library(rows)
    print("validator", code, {key: len(items) for key, items in grouped.items()})
    if code != 0:
        return code
    by_session = {str(row.get("session_id") or ""): row for row in rows}
    youtube = build_youtube_client_for_page("aiwake", enforce_channel=True)
    ids = []
    for row in rows:
        video_id = str((((row.get("platform_overrides") or {}).get("youtube") or {}).get("video_id") or ""))
        if video_id:
            ids.append(video_id)
    try:
        snippets = _load_snippets(youtube, ids)
    except HttpError as exc:
        print("list failed", str(exc)[:200])
        return 1
    live: dict[str, dict[str, Any]] = {}
    for video_id, item in snippets.items():
        status = item.get("status") or {}
        live[video_id] = {
            "video_id": video_id,
            "privacy": status.get("privacyStatus") or "private",
            "publish_at": status.get("publishAt") or "",
            "title": (item.get("snippet") or {}).get("title") or "",
        }
    twenty = json.loads(TWENTY_IN.read_text(encoding="utf-8"))
    plan = build_plan(rows, twenty, live, now=datetime.now(BRT))
    print("plan", len(plan), "uploads", sum(1 for item in plan if item["action"] == "upload"))
    OUT.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    quota = False

    def remember(row: dict[str, Any] | None, video_id: str, when: str) -> None:
        if row is None or not video_id:
            return
        _upsert(
            row,
            {
                "platform": "youtube",
                "channel": f"{CHANNEL} ({CHANNEL_ID})",
                "video_id": video_id,
                "url": f"https://youtu.be/{video_id}",
                "file_md5": _known_md5(row),
                "publish_time_utc": when,
                "status": "scheduled" if when else "uploaded_private",
                "updated_at": _now(),
            },
        )
        if when:
            dist = row.setdefault("distribution", {}).setdefault("youtube", {})
            dist["status"] = "scheduled"
            dist["scheduled_time_utc"] = when
            dist["updated_at"] = _now()

    targets = []
    planned_ids = set()
    for item in plan:
        if item["action"] != "reschedule" or not item["video_id"]:
            continue
        info = live.get(item["video_id"]) or {}
        targets.append(
            {
                "session_id": item["session_id"],
                "video_id": item["video_id"],
                "title": item["title"],
                "description": item["description"],
                "publish_at": "" if info.get("privacy") == "public" else item["slot_utc"],
                "slot_brt": item["slot_brt"],
                "action": "keep_public" if info.get("privacy") == "public" else "reschedule",
            }
        )
        planned_ids.add(item["video_id"])
    for row in rows:
        block = ((row.get("platform_overrides") or {}).get("youtube") or {})
        video_id = str(block.get("video_id") or "")
        if not video_id or video_id in planned_ids:
            continue
        targets.append(
            {
                "session_id": str(row.get("session_id") or ""),
                "video_id": video_id,
                "title": str(block.get("title") or ""),
                "description": str(block.get("caption") or ""),
                "publish_at": "",
                "slot_brt": "",
                "action": "snippet",
            }
        )
    for index, target in enumerate(targets, start=1):
        video_id = target["video_id"]
        item = snippets.get(video_id)
        if item is None:
            print("missing", video_id)
            results.append({**target, "url": "", "error": "missing"})
            continue
        if not target["title"] or not target["description"]:
            print("empty caption", video_id)
            continue
        status = item.get("status") or {}
        keep_privacy = str(status.get("privacyStatus") or "") == "public"
        publish_at = "" if keep_privacy else target["publish_at"]
        if _matches(item, target["title"], target["description"], publish_at):
            print("unchanged", video_id)
            if publish_at:
                remember(by_session.get(target["session_id"]), video_id, publish_at)
                results.append({**target, "url": f"https://youtu.be/{video_id}", "action": target["action"]})
            continue
        try:
            _push(
                youtube,
                video_id,
                target["title"],
                target["description"],
                item.get("snippet") or {},
                publish_at,
                keep_privacy=keep_privacy,
            )
        except HttpError as exc:
            if _is_quota(exc):
                print("quota stop", video_id)
                quota = True
                results.append({**target, "action": "quota", "error": "quotaExceeded"})
                break
            print("update failed", video_id, str(exc)[:180])
            results.append({**target, "action": "update_failed", "error": str(exc)[:200]})
            continue
        if publish_at:
            remember(by_session.get(target["session_id"]), video_id, publish_at)
        print(target["slot_brt"] or "snippet", target["action"], video_id)
        results.append({**target, "url": f"https://youtu.be/{video_id}"})
        if index % 10 == 0:
            save_distribution_library(LIBRARY, rows)
            print("saved", index)
    if quota:
        save_distribution_library(LIBRARY, rows)
        (OUT / "final_schedule.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
        _write_report(results, "YouTube quota stopped the run before every video was updated.")
        print("stopped on quota", len(results))
        return 2

    known = _youtube_md5s(rows)
    for item in plan:
        if item["action"] != "upload":
            continue
        digest = _md5(item["path"])
        if digest and digest in known:
            print("skip md5", item["session_id"])
            results.append(_row_result(item, action="skip_duplicate_md5"))
            continue
        if item["video_id"] in {"_KnJUrdA9Xk", "8h4lif5wQ98", "060lEpr4Gdg", "QjXnEGYxj9o"}:
            print("refusing reupload", item["video_id"])
            continue
        try:
            video_id, url, _when = upload_short(
                item["path"],
                item["title"],
                item["description"],
                tags=[tag.lstrip("#") for tag in item["description"].split() if tag.startswith("#")],
                privacy_status="private",
                publish_at=item["slot"].astimezone(timezone.utc),
                category_id="28",
                page_name="aiwake",
                youtube=youtube,
                skip_playlist=True,
                preserve_title=True,
            )
        except Exception as exc:  # noqa: BLE001
            action = "quota" if _is_quota(exc) else "upload_failed"
            results.append(_row_result(item, action=action, error=str(exc)[:240]))
            print(action, item["session_id"], str(exc)[:160])
            if action == "quota":
                quota = True
                break
            continue
        if digest:
            known.add(digest)
        row = by_session.get(item["session_id"])
        if row is not None:
            block = row.setdefault("platform_overrides", {}).setdefault("youtube", {})
            block["video_id"] = video_id
            remember(row, video_id, item["slot_utc"])
        print(item["slot_brt"], "upload", video_id)
        results.append(
            _row_result(
                {**item, "video_id": video_id, "url": url},
                action="upload",
            )
        )
    save_distribution_library(LIBRARY, rows)
    (OUT / "final_schedule.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    note = "Library validator returned 0 before this run."
    if quota:
        note = "Uploads stopped when the YouTube quota ran out. Existing videos were updated first."
    _write_report(results, note)
    print("done", len(results), "quota", quota)
    return 2 if quota else 0


def main() -> int:
    rows = load_distribution_library(LIBRARY)
    ready = [
        row
        for row in rows
        if str(row.get("production_status") or "") == "ready"
        and str((row.get("caption_qa") or {}).get("status") or "") == "ok"
    ]
    picked = random.Random(20261005).sample(ready, 10)
    before = []
    for row in picked:
        youtube = ((row.get("platform_overrides") or {}).get("youtube") or {})
        before.append(
            {
                "session_id": row.get("session_id"),
                "title": youtube.get("title") or "",
                "caption": youtube.get("caption") or "",
            }
        )
    backup = _backup(LIBRARY)
    count = rewrite_ready_rows(rows)
    code, grouped = validate_library(rows)
    print("rewritten", count, "validator", code, {key: len(items) for key, items in grouped.items()})
    if code != 0:
        print("not saved; backup", backup.name)
        return code
    save_distribution_library(LIBRARY, rows)
    after = []
    by_session = {str(row.get("session_id") or ""): row for row in rows}
    for item in before:
        row = by_session[str(item["session_id"])]
        youtube = ((row.get("platform_overrides") or {}).get("youtube") or {})
        after.append(
            {
                "session_id": item["session_id"],
                "before_title": item["title"],
                "after_title": youtube.get("title") or "",
                "before_caption": item["caption"],
                "after_caption": youtube.get("caption") or "",
            }
        )
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "final_before_after.json").write_text(json.dumps(after, indent=2, ensure_ascii=False), encoding="utf-8")
    print("saved", backup.name)

    youtube = build_youtube_client_for_page("aiwake", enforce_channel=True)
    uploads = list_channel_upload_videos(youtube, max_pages=20)
    live = {item["video_id"]: item for item in uploads}
    ids = []
    for row in rows:
        video_id = str((((row.get("platform_overrides") or {}).get("youtube") or {}).get("video_id") or ""))
        if video_id:
            ids.append(video_id)
    for video_id in ids:
        if video_id not in live:
            live[video_id] = {"video_id": video_id, "privacy": "private", "publish_at": ""}
    snippets = _load_snippets(youtube, ids)
    updated = 0
    for row in rows:
        block = ((row.get("platform_overrides") or {}).get("youtube") or {})
        video_id = str(block.get("video_id") or "")
        title = str(block.get("title") or "")
        description = str(block.get("caption") or "")
        if not video_id or not title or not description:
            continue
        if video_id not in snippets:
            print("missing", video_id)
            continue
        item = snippets[video_id]
        status = item.get("status") or {}
        live[video_id] = {
            "video_id": video_id,
            "privacy": status.get("privacyStatus") or live.get(video_id, {}).get("privacy") or "private",
            "publish_at": status.get("publishAt") or "",
            "title": (item.get("snippet") or {}).get("title") or "",
        }
        _snippet(youtube, video_id, title, description, item.get("snippet") or {})
        updated += 1
        if updated % 20 == 0:
            print("snippets", updated)
    print("snippets", updated)

    ledger = json.loads(LEDGER_IN.read_text(encoding="utf-8"))
    for entry in ledger.get("entries") or []:
        row = by_session.get(str(entry.get("session_id") or ""))
        if row is None:
            continue
        _upsert(
            row,
            {
                "platform": "tiktok",
                "channel": str(entry.get("channel") or "Aiwake TikTok"),
                "video_id": str(entry.get("post_id") or ""),
                "url": str(entry.get("tiktok_url") or ""),
                "file_md5": str(entry.get("video_md5") or ""),
                "publish_time_utc": str(entry.get("publish_time_utc") or ""),
                "status": str(entry.get("status") or ""),
                "updated_at": _now(),
            },
        )
        dist = row.setdefault("distribution", {}).setdefault("tiktok", {})
        dist["status"] = str(entry.get("status") or "")
        dist["updated_at"] = _now()
    for row in rows:
        block = ((row.get("platform_overrides") or {}).get("youtube") or {})
        video_id = str(block.get("video_id") or "")
        if not video_id:
            continue
        info = live.get(video_id) or {}
        privacy = str(info.get("privacy") or "private")
        publish_at = str(info.get("publish_at") or "")
        if privacy == "public":
            status = "published"
        elif publish_at:
            status = "scheduled"
        else:
            status = "uploaded_private"
        _upsert(
            row,
            {
                "platform": "youtube",
                "channel": f"{CHANNEL} ({CHANNEL_ID})",
                "video_id": video_id,
                "url": f"https://youtu.be/{video_id}",
                "file_md5": _md5(str(row.get("video_path") or "")),
                "publish_time_utc": publish_at,
                "status": status,
                "updated_at": _now(),
            },
        )
    save_distribution_library(LIBRARY, rows)

    twenty = json.loads(TWENTY_IN.read_text(encoding="utf-8"))
    plan = build_plan(rows, twenty, live, now=datetime.now(BRT))
    known = _youtube_md5s(rows)
    results = []
    for item in plan:
        action = item["action"]
        video_id = item["video_id"]
        if action == "upload":
            digest = _md5(item["path"])
            if digest and digest in known:
                action = "skip_duplicate_md5"
                results.append({**item, "action": action, "slot": item["slot_brt"]})
                print("skip md5", item["session_id"])
                continue
            if video_id in {"_KnJUrdA9Xk", "8h4lif5wQ98", "060lEpr4Gdg", "QjXnEGYxj9o"}:
                action = "reschedule"
            else:
                try:
                    video_id, url, _when = upload_short(
                        item["path"],
                        item["title"],
                        item["description"],
                        tags=[tag.lstrip("#") for tag in item["description"].split() if tag.startswith("#")],
                        privacy_status="private",
                        publish_at=item["slot"].astimezone(timezone.utc),
                        category_id="28",
                        page_name="aiwake",
                        youtube=youtube,
                        skip_playlist=True,
                        preserve_title=True,
                    )
                except Exception as exc:  # noqa: BLE001
                    results.append({**item, "action": "upload_failed", "error": str(exc)[:240], "slot": item["slot_brt"]})
                    print("upload failed", item["session_id"], str(exc)[:160])
                    continue
                if digest:
                    known.add(digest)
                row = by_session[item["session_id"]]
                block = row.setdefault("platform_overrides", {}).setdefault("youtube", {})
                block["video_id"] = video_id
                item["video_id"] = video_id
                item["url"] = url
        if action == "reschedule" and video_id:
            update_video_publish_at(youtube, video_id, item["slot"].astimezone(timezone.utc), privacy_status="private")
            item["url"] = f"https://youtu.be/{video_id}"
        row = by_session.get(item["session_id"])
        if row is not None and video_id:
            _upsert(
                row,
                {
                    "platform": "youtube",
                    "channel": f"{CHANNEL} ({CHANNEL_ID})",
                    "video_id": video_id,
                    "url": f"https://youtu.be/{video_id}",
                    "file_md5": _md5(str(row.get("video_path") or "")),
                    "publish_time_utc": item["slot_utc"],
                    "status": "scheduled",
                    "updated_at": _now(),
                },
            )
            dist = row.setdefault("distribution", {}).setdefault("youtube", {})
            dist["status"] = "scheduled"
            dist["scheduled_time_utc"] = item["slot_utc"]
            dist["updated_at"] = _now()
        results.append(
            {
                "session_id": item["session_id"],
                "video_id": video_id,
                "url": item.get("url") or (f"https://youtu.be/{video_id}" if video_id else ""),
                "action": action,
                "slot_brt": item["slot_brt"],
                "title": item["title"],
            }
        )
        print(item["slot_brt"], action, video_id)
    save_distribution_library(LIBRARY, rows)
    (OUT / "final_schedule.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print("scheduled", len(results))
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "resume":
        raise SystemExit(resume())
    raise SystemExit(main())
