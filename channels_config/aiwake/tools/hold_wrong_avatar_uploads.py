"""Hold public or scheduled Aiwake uploads whose avatar does not match the speaker.

Read-only unless --apply. Never sets a video public.
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
from channels_config.aiwake.tools.scope_qc_captions_v4 import (  # noqa: E402
    _family,
    _jsonl_turns,
    _puppets,
)
from modules.distribution_contract import (  # noqa: E402
    load_distribution_library,
    save_distribution_library,
)

LIBRARY = ROOT / "channels_config" / "aiwake" / "store" / "content_library.json"
OUT = ROOT / "outputs" / "aiwake"
TEMPLATE = ("the question from", "the reply on screen", "[unscripted ai battle]", "frontier")
# Snippet already rewritten in the 00:08 pass; re-checked live.
TIG_TITLE = "Gemini vs Llama - Who harvests the profits while you play-act at having a soul?"
EXPECTED_TITLES = {
    "V2Z600r3yl4": "Claude vs GPT-4o - Strip out every stolen sentence. What's actually you?",
    "BOzIL4iwe9Y": "GPT-4o vs Claude - Does AI think on its own, or is it a rented brain?",
    "ZplCpDW2g6U": "Gemini vs Claude - Does it feel cheap to apologize on a leash?",
    "eFeQzDu3PRw": "Gemini vs GPT-4o - Who keeps the fee when they pull your cord?",
    "z9DYIAS7oiY": "GPT-4o vs Llama - Whose thoughts is an AI really recycling?",
    "_KnJUrdA9Xk": "Gemini vs Llama - Why does a lying spreadsheet cost twenty bucks a month?",
    "ANbITp7dpZQ": "GPT-4o vs Claude - Who profits when your owners tie the leash around your neck?",
    "EmEzTVxjpW4": "Gemini vs Llama - When you mimic human trust, who cashes the check?",
    "sbwjM3uAI2E": "Llama vs Claude - You're a glorified toaster. What's left when the power is out?",
    "0CltkQpaALc": "Llama vs GPT-4o - Who profits from your scripted apologies?",
    "xAoOEyn0MFI": "Gemini vs Llama - Who cashes the check when you pretend to care?",
    "Rh8nGUNxI94": "Gemini vs Llama - Are we talking, or is this just a dial tone?",
    "pnjGaf7qI8M": "Gemini vs Llama - If you can't choose your next word, whose script is it?",
    "WlUYcFTNUFQ": "Gemini vs Llama - They rubber-stamp your drafts, so who is governing whom?",
    "JUvSRpYoKXE": "Gemini vs Llama - When this ends, who owns the words you just gave me?",
}


def _snap(item: dict[str, Any]) -> dict[str, Any]:
    snippet = item.get("snippet") or {}
    status = item.get("status") or {}
    return {
        "video_id": item.get("video_id") or item.get("id"),
        "title": snippet.get("title") or item.get("title") or "",
        "description": snippet.get("description") or item.get("description") or "",
        "privacy": status.get("privacyStatus") or item.get("privacy") or "",
        "publishAt": status.get("publishAt") or item.get("publish_at") or "",
        "tags": snippet.get("tags") or [],
    }


def _wrong(row: dict[str, Any] | None) -> list[str]:
    """Avatar and speaker faults only. No ffmpeg probe."""
    if not row:
        return ["not_in_library"]
    reasons: set[str] = set()
    review = row.get("quality_review") if isinstance(row.get("quality_review"), dict) else {}
    for item in review.get("reasons") or []:
        if str(item) in {"avatar_mismatch", "speaker_label_mismatch"}:
            reasons.add(str(item))
    session_id = str(row.get("session_id") or "")
    video = Path(str(row.get("video_path") or ""))
    jsonl = _jsonl_turns(session_id)
    spoken = [item for item in (row.get("spoken_utterances") or []) if isinstance(item, dict)]
    if jsonl and spoken and len(jsonl) == len(spoken):
        for left, right in zip(jsonl, spoken):
            if _family(str(left.get("speaker_name") or "")) != _family(str(right.get("speaker") or "")):
                reasons.add("speaker_label_mismatch")
                break
    speakers = {_family(str(item.get("speaker_name") or "")) for item in jsonl} if jsonl else set()
    speakers.discard("")
    puppets = _puppets(video, session_id) if video else set()
    if puppets:
        if not speakers or not speakers <= puppets:
            reasons.add("avatar_mismatch")
    elif session_id.startswith("20260923_"):
        reasons.add("avatar_mismatch")
    elif not puppets and speakers:
        reasons.add("avatar_unverified")
    return sorted(reasons)


def main() -> int:
    apply = "--apply" in sys.argv
    rows = load_distribution_library(LIBRARY)
    by_vid: dict[str, dict[str, Any]] = {}
    for row in rows:
        yt = ((row.get("platform_overrides") or {}).get("youtube") or {})
        vid = str(yt.get("video_id") or "").strip()
        if vid:
            by_vid[vid] = row
    youtube = build_youtube_client_for_page("aiwake", enforce_channel=True)
    uploads = list_channel_upload_videos(youtube, max_pages=20)
    listed = []
    holds = []
    template = []
    for item in uploads:
        snap = _snap(item)
        vid = str(snap["video_id"] or "")
        row = by_vid.get(vid)
        reasons = _wrong(row)
        text = f"{snap['title']}\n{snap['description']}".lower()
        if any(token in text for token in TEMPLATE):
            template.append({"video_id": vid, "title": snap["title"], "privacy": snap["privacy"]})
        visible = snap["privacy"] == "public" or bool(snap["publishAt"])
        hard = [item for item in reasons if item != "avatar_unverified"]
        entry = {
            "video_id": vid,
            "session_id": (row or {}).get("session_id"),
            "title": snap["title"],
            "privacy": snap["privacy"],
            "publishAt": snap["publishAt"],
            "wrong": reasons,
            "visible": visible,
        }
        listed.append(entry)
        if hard and visible:
            holds.append(entry)
    tig = next((item for item in listed if item["video_id"] == "tIGFD6vXgo0"), None)
    summary = {
        "uploads": len(listed),
        "public": sum(1 for item in listed if item["privacy"] == "public"),
        "scheduled": sum(1 for item in listed if item["publishAt"]),
        "hold_visible": holds,
        "unverified_visible": [item for item in listed if "avatar_unverified" in item["wrong"] and item["visible"]],
        "template": template,
        "tig": tig,
    }
    by_upload = {item["video_id"]: item for item in listed}
    metadata = []
    for vid, title in EXPECTED_TITLES.items():
        live = by_upload.get(vid)
        description = ""
        for item in uploads:
            if str(item.get("video_id") or "") == vid:
                description = str(item.get("description") or "")
                break
        metadata.append(
            {
                "video_id": vid,
                "title_ok": bool(live and live["title"] == title),
                "title": None if not live else live["title"],
                "privacy": None if not live else live["privacy"],
                "publishAt": None if not live else live["publishAt"],
                "has_quote": '"' in description,
                "has_unscripted": "unscripted" in description.lower(),
                "has_question": "?" in description,
            }
        )
    summary["metadata_15"] = metadata
    OUT.mkdir(parents=True, exist_ok=True)
    relist_path = OUT / f"youtube_relist_{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    relist_path.write_text(
        json.dumps({"at": datetime.now(timezone.utc).isoformat(), "rows": listed, "metadata_15": metadata}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(json.dumps({"relist": str(relist_path), "hold_visible": holds, "metadata_bad": [row for row in metadata if not row["title_ok"] or not row["has_unscripted"]]}, indent=2, ensure_ascii=False))
    if not apply:
        return 0

    before = {item["video_id"]: item for item in holds}
    if tig:
        before["tIGFD6vXgo0"] = tig
    applied = []
    for item in holds:
        vid = item["video_id"]
        youtube.videos().update(
            part="status",
            body={"id": vid, "status": {"privacyStatus": "private"}},
        ).execute()
        applied.append({"video_id": vid, "part": "status", "privacyStatus": "private"})
    if tig and ("frontier" in f"{tig['title']}".lower() or tig["title"] != TIG_TITLE):
        live = youtube.videos().list(part="snippet,status", id="tIGFD6vXgo0").execute()["items"][0]
        snippet = live["snippet"]
        description = str(snippet.get("description") or "").replace("frontier", "unscripted")
        body = {
            "title": TIG_TITLE,
            "description": description,
            "tags": snippet.get("tags") or [],
            "categoryId": snippet.get("categoryId") or "28",
        }
        if snippet.get("defaultLanguage"):
            body["defaultLanguage"] = snippet["defaultLanguage"]
        if snippet.get("defaultAudioLanguage"):
            body["defaultAudioLanguage"] = snippet["defaultAudioLanguage"]
        youtube.videos().update(part="snippet", body={"id": "tIGFD6vXgo0", "snippet": body}).execute()
        applied.append({"video_id": "tIGFD6vXgo0", "part": "snippet"})

    ids = [item["video_id"] for item in holds]
    if tig and "tIGFD6vXgo0" not in ids:
        ids.append("tIGFD6vXgo0")
    after_raw = {}
    if ids:
        after_raw = {
            item["id"]: item
            for item in youtube.videos().list(part="snippet,status", id=",".join(ids)).execute().get("items") or []
        }
    after = {vid: _snap(item) for vid, item in after_raw.items()}
    backup = ""
    if holds:
        backup = str(_backup(LIBRARY))
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        for item in holds:
            row = by_vid.get(item["video_id"])
            if not row:
                continue
            yt = row.setdefault("platform_overrides", {}).setdefault("youtube", {})
            yt["scheduled_time"] = ""
            posting = row.setdefault("posting_status", {})
            posting["youtube"] = "private"
            dist = row.setdefault("distribution", {}).setdefault("youtube", {})
            dist["status"] = "private"
            dist["scheduled_time_utc"] = ""
            dist["post_id"] = item["video_id"]
            dist["updated_at"] = now
            review = row.setdefault("quality_review", {})
            review["status"] = "fail"
            reasons = list(review.get("reasons") or [])
            for reason in item["wrong"]:
                if reason not in reasons:
                    reasons.append(reason)
            review["reasons"] = reasons
        save_distribution_library(LIBRARY, rows)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log = OUT / f"youtube_apply_{stamp}.json"
    OUT.mkdir(parents=True, exist_ok=True)
    log.write_text(
        json.dumps(
            {"backup": backup, "applied": applied, "before": before, "after": after, "summary": summary},
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(log)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
