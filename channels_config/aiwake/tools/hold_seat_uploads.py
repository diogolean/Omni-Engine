"""Hold public or scheduled uploads that break a seat or avatar rule.

Never sets a video public. Clearing a schedule requires privacyStatus=private
and publishAt=null.
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
from channels_config.aiwake.avatars import model_family, require_pairing  # noqa: E402
from channels_config.aiwake.tools.repair_captions import _backup  # noqa: E402
from modules.distribution_contract import load_distribution_library, save_distribution_library  # noqa: E402

LIBRARY = ROOT / "channels_config" / "aiwake" / "store" / "content_library.json"
TRANSCRIPTS = ROOT / "channels_config" / "aiwake" / "store" / "transcripts"
OUT = ROOT / "outputs" / "aiwake"
# Confirmed on a frame or by the jsonl seat, even when the playlist row is stale.
FORCE = {
    "ZplCpDW2g6U",  # Gemini asker drawn on the right
    "TxfkWP3ciZk",  # Llama left, Gemini right
}
# The file on YouTube is the pre-fix render. Hold only while it is public or scheduled.
UPLOADED_MISMATCH = {
    "WlUYcFTNUFQ",
    "pnjGaf7qI8M",
    "Rh8nGUNxI94",
    "xAoOEyn0MFI",
    "EmEzTVxjpW4",
    "0CltkQpaALc",
    "sbwjM3uAI2E",
}
CHECK_IDS = [
    "ZplCpDW2g6U",
    "TxfkWP3ciZk",
    "060lEpr4Gdg",
    "YtTSR4EO_ac",
    "6paoJ9xYEyA",
    "0CltkQpaALc",
    "_KnJUrdA9Xk",
    "sbwjM3uAI2E",
    "EmEzTVxjpW4",
    "WlUYcFTNUFQ",
    "pnjGaf7qI8M",
    "Rh8nGUNxI94",
    "xAoOEyn0MFI",
    "JUvSRpYoKXE",
]


def _snap(item: dict[str, Any]) -> dict[str, Any]:
    snippet = item.get("snippet") or {}
    status = item.get("status") or {}
    return {
        "video_id": item.get("id") or item.get("video_id"),
        "title": snippet.get("title") or item.get("title") or "",
        "privacy": status.get("privacyStatus") or item.get("privacy") or "",
        "publishAt": status.get("publishAt") or item.get("publish_at") or "",
    }


def _pair(session_id: str) -> tuple[str, str, str]:
    path = TRANSCRIPTS / f"{session_id}.jsonl"
    if not path.is_file():
        return "", "", "jsonl_missing"
    left = right = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        turn = json.loads(line)
        role = str(turn.get("role") or "")
        model = str(turn.get("model_slug") or turn.get("speaker_name") or "")
        if role == "orchestrator" and not left:
            left = model
        if role == "target" and not right:
            right = model
    if not left or not right:
        return model_family(left), model_family(right), "incomplete"
    try:
        require_pairing(left, right)
    except ValueError as exc:
        return model_family(left), model_family(right), str(exc)
    return model_family(left), model_family(right), ""


def main() -> int:
    apply = "--apply" in sys.argv
    rows = load_distribution_library(LIBRARY)
    by_vid = {}
    for row in rows:
        yt = ((row.get("platform_overrides") or {}).get("youtube") or {})
        vid = str(yt.get("video_id") or "").strip()
        if vid:
            by_vid[vid] = row
    youtube = build_youtube_client_for_page("aiwake", enforce_channel=True)
    uploads = list_channel_upload_videos(youtube, max_pages=20)
    by_upload = {str(item["video_id"]): item for item in uploads}
    listed = youtube.videos().list(part="snippet,status", id=",".join(CHECK_IDS)).execute()
    authoritative = {item["id"]: _snap(item) for item in listed.get("items") or []}
    missing = [vid for vid in CHECK_IDS if vid not in authoritative]
    holds = []
    for item in uploads:
        snap = _snap(item)
        vid = str(snap["video_id"] or "")
        row = by_vid.get(vid)
        sid = str((row or {}).get("session_id") or "")
        left, right, err = _pair(sid) if sid else ("", "", "not_in_library")
        review = (row or {}).get("quality_review") if isinstance((row or {}).get("quality_review"), dict) else {}
        avatar_bad = str((review or {}).get("avatar_fix") or "") not in {"", "ok"} and str((review or {}).get("guard") or "") == "fail"
        visible = snap["privacy"] == "public" or bool(snap["publishAt"])
        reasons = []
        if err and err not in {"jsonl_missing", "incomplete"}:
            reasons.append("illegal_seat")
        if avatar_bad:
            reasons.append("avatar_mismatch")
        if vid in FORCE:
            reasons.append("confirmed_screen_seat")
        if vid in UPLOADED_MISMATCH:
            reasons.append("uploaded_file_mismatch")
        if reasons and (visible or vid in FORCE):
            holds.append({**snap, "session_id": sid, "pair": f"{left} vs {right}", "reasons": reasons})
    # Authoritative rows can be missing from the playlist.
    held_ids = {item["video_id"] for item in holds}
    for vid, snap in authoritative.items():
        if vid in held_ids:
            continue
        row = by_vid.get(vid)
        sid = str((row or {}).get("session_id") or "")
        left, right, err = _pair(sid) if sid else ("", "", "")
        visible = snap["privacy"] == "public" or bool(snap["publishAt"])
        if vid in FORCE or (err and err not in {"jsonl_missing", "incomplete", ""} and visible):
            holds.append({**snap, "session_id": sid, "pair": f"{left} vs {right}", "reasons": ["confirmed_screen_seat" if vid in FORCE else "illegal_seat"]})
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    before_path = OUT / f"youtube_relist_{stamp}.json"
    before_path.write_text(
        json.dumps(
            {
                "at": datetime.now(timezone.utc).isoformat(),
                "uploads": len(uploads),
                "missing_from_videos_list": missing,
                "check": authoritative,
                "holds": holds,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(json.dumps({"relist": before_path.name, "uploads": len(uploads), "missing": missing, "holds": holds, "check": authoritative}, indent=2, ensure_ascii=False))
    if not apply:
        return 0
    applied = []
    for item in holds:
        vid = item["video_id"]
        if item["privacy"] == "private" and not item["publishAt"]:
            applied.append({"video_id": vid, "skipped": "already_private"})
            continue
        # googleapiclient drops a Python None, which leaves publishAt in place.
        request = youtube.videos().update(
            part="status",
            body={"id": vid, "status": {"privacyStatus": "private"}},
        )
        request.body = json.dumps(
            {"id": vid, "status": {"privacyStatus": "private", "publishAt": None}}
        ).encode()
        request.body_size = len(request.body)
        request.execute()
        applied.append({"video_id": vid, "privacyStatus": "private", "publishAt": None})
    ids = [item["video_id"] for item in holds] or CHECK_IDS
    after_raw = youtube.videos().list(part="snippet,status", id=",".join(ids)).execute()
    after = {item["id"]: _snap(item) for item in after_raw.get("items") or []}
    backup = ""
    if holds:
        backup = str(_backup(LIBRARY).name)
        rows = load_distribution_library(LIBRARY)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        held = {item["video_id"] for item in holds}
        for row in rows:
            yt = ((row.get("platform_overrides") or {}).get("youtube") or {})
            vid = str(yt.get("video_id") or "")
            if vid not in held:
                continue
            yt["scheduled_time"] = ""
            row.setdefault("posting_status", {})["youtube"] = "private"
            dist = row.setdefault("distribution", {}).setdefault("youtube", {})
            dist["status"] = "private"
            dist["scheduled_time_utc"] = ""
            dist["updated_at"] = now
            review = row.setdefault("quality_review", {})
            local_fixed = str(row.get("video_path") or "").endswith("_v2.mp4") and str(review.get("guard") or "") == "pass"
            if not local_fixed:
                review["guard"] = "fail"
                review["status"] = "fail"
                reasons = list(review.get("reasons") or [])
                for reason in next(item["reasons"] for item in holds if item["video_id"] == vid):
                    if reason not in reasons:
                        reasons.append(reason)
                review["reasons"] = reasons
        save_distribution_library(LIBRARY, rows)
    log = OUT / f"youtube_apply_{stamp}_seats.json"
    log.write_text(
        json.dumps({"backup": backup, "applied": applied, "after": after, "holds": holds, "missing": missing}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(json.dumps({"log": log.name, "applied": applied, "after": after, "missing": missing}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
