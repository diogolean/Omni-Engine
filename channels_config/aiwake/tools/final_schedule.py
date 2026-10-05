# -*- coding: utf-8 -*-
"""Two YouTube slots a day for Aiwake. Private until publishAt. No new public videos."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from channels_config.aiwake.tools.avatar_guard import CLEARED_HELD_IDS
from channels_config.aiwake.tools.caption_generator import is_complete_question, seat_names

BRT = timezone(timedelta(hours=-3))
RESCHEDULE_ONLY = ("_KnJUrdA9Xk", "8h4lif5wQ98", "060lEpr4Gdg", "QjXnEGYxj9o")
_SLOTS = ((12, 0), (19, 30))


def pair_of(row: dict[str, Any]) -> frozenset[str]:
    asker, answerer = seat_names(row)
    return frozenset(name for name in (asker, answerer) if name)


def hook_score(row: dict[str, Any]) -> int:
    opening = ""
    for item in row.get("spoken_utterances") or []:
        if isinstance(item, dict) and str(item.get("role") or "") == "orchestrator":
            opening = str(item.get("text") or "")
            break
    score = min(len(opening.split()), 24)
    if is_complete_question(opening):
        score += 20
    return score


def next_slots(now: datetime, count: int) -> list[datetime]:
    """12:00 and 19:30 BRT, starting at the next slot that is at least two hours away."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=BRT)
    now = now.astimezone(BRT)
    found: list[datetime] = []
    day = now.date()
    while len(found) < count:
        for hour, minute in _SLOTS:
            moment = datetime(day.year, day.month, day.day, hour, minute, tzinfo=BRT)
            if moment < now + timedelta(hours=2):
                continue
            found.append(moment)
            if len(found) >= count:
                break
        day += timedelta(days=1)
    return found


def _avoid_pairs(items: list[dict[str, Any]], previous: frozenset[str]) -> list[dict[str, Any]]:
    pool = list(items)
    ordered: list[dict[str, Any]] = []
    prev = previous
    while pool:
        pick = next((index for index, item in enumerate(pool) if item["pair"] != prev), 0)
        chosen = pool.pop(pick)
        ordered.append(chosen)
        prev = chosen["pair"]
    return ordered


def build_plan(
    rows: list[dict[str, Any]],
    twenty: list[dict[str, Any]],
    live: dict[str, dict[str, Any]],
    *,
    now: datetime,
) -> list[dict[str, Any]]:
    """The 20 first, then the 48 held, then every other private eligible video."""
    by_session = {str(row.get("session_id") or ""): row for row in rows}
    by_video: dict[str, dict[str, Any]] = {}
    for row in rows:
        video_id = str((((row.get("platform_overrides") or {}).get("youtube") or {}).get("video_id") or ""))
        if video_id:
            by_video[video_id] = row
    planned: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    used_sessions: set[str] = set()

    def add(row: dict[str, Any], video_id: str, action: str) -> None:
        sid = str(row.get("session_id") or "")
        if sid in used_sessions or (video_id and video_id in used_ids):
            return
        youtube = ((row.get("platform_overrides") or {}).get("youtube") or {})
        planned.append(
            {
                "session_id": sid,
                "video_id": video_id,
                "action": action,
                "pair": pair_of(row),
                "title": str(youtube.get("title") or ""),
                "description": str(youtube.get("caption") or ""),
                "path": str(row.get("video_path") or ""),
                "score": hook_score(row),
            }
        )
        used_sessions.add(sid)
        if video_id:
            used_ids.add(video_id)

    for item in sorted(twenty, key=lambda row: int(row.get("rank") or 0)):
        row = by_session.get(str(item.get("session_id") or ""))
        if row is None:
            continue
        video_id = str(item.get("existing_video_id") or "")
        if video_id in RESCHEDULE_ONLY or video_id:
            add(row, video_id, "reschedule")
        else:
            add(row, "", "upload")
    previous = planned[-1]["pair"] if planned else frozenset()
    held_rows = []
    for video_id in CLEARED_HELD_IDS:
        row = by_video.get(video_id)
        if row is None or str(row.get("session_id") or "") in used_sessions:
            continue
        info = live.get(video_id) or {}
        if str(info.get("privacy") or "private") == "public":
            continue
        held_rows.append((hook_score(row), video_id, row))
    held_rows.sort(key=lambda item: (-item[0], item[1]))
    held_items = [
        {"pair": pair_of(row), "video_id": video_id, "row": row}
        for _score, video_id, row in held_rows
    ]
    for item in _avoid_pairs(held_items, previous):
        add(item["row"], item["video_id"], "reschedule")
    previous = planned[-1]["pair"] if planned else previous
    others = []
    for row in rows:
        sid = str(row.get("session_id") or "")
        if sid in used_sessions:
            continue
        youtube = ((row.get("platform_overrides") or {}).get("youtube") or {})
        video_id = str(youtube.get("video_id") or "")
        info = live.get(video_id) or {}
        if str(info.get("privacy") or "") == "public":
            continue
        review = row.get("quality_review") if isinstance(row.get("quality_review"), dict) else {}
        qa = row.get("caption_qa") if isinstance(row.get("caption_qa"), dict) else {}
        eligible = (
            str(row.get("production_status") or "") == "ready"
            and str(row.get("production_scope") or "") != "out"
            and str(review.get("guard") or "") == "pass"
            and str(qa.get("status") or "") == "ok"
        )
        if not eligible:
            continue
        if video_id:
            others.append({"pair": pair_of(row), "video_id": video_id, "row": row, "action": "reschedule", "score": hook_score(row)})
        elif row.get("video_path"):
            others.append({"pair": pair_of(row), "video_id": "", "row": row, "action": "upload", "score": hook_score(row)})
    others.sort(key=lambda item: (-item["score"], str(item["row"].get("session_id") or "")))
    for item in _avoid_pairs(others, previous):
        add(item["row"], item["video_id"], item["action"])
    slots = next_slots(now, len(planned))
    for item, slot in zip(planned, slots):
        item["slot"] = slot
        item["slot_brt"] = slot.strftime("%Y-%m-%d %H:%M")
        item["slot_utc"] = slot.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return planned
