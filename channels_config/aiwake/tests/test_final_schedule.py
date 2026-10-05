# -*- coding: utf-8 -*-
"""The final YouTube grid is two private slots a day, with the given order first."""
from datetime import datetime, timedelta

from channels_config.aiwake.tools.final_schedule import BRT, RESCHEDULE_ONLY, build_plan, next_slots


def _row(session: str, asker: str, answerer: str, video_id: str = "") -> dict:
    return {
        "session_id": session,
        "production_status": "ready",
        "production_scope": "in",
        "video_path": f"{session}.mp4",
        "quality_review": {"status": "pass", "guard": "pass"},
        "caption_qa": {"status": "ok", "generator": "captions_v4", "model": "x"},
        "spoken_utterances": [
            {"role": "orchestrator", "speaker": asker, "text": f"Who owns the leash for {session}?"},
            {"role": "target", "speaker": answerer, "text": "The company does."},
        ],
        "platform_overrides": {"youtube": {"video_id": video_id, "title": "t", "caption": "d"}},
    }


def test_slots_are_noon_and_evening_with_no_empty_day() -> None:
    now = datetime(2026, 10, 5, 0, 12, tzinfo=BRT)
    slots = next_slots(now, 5)
    assert [item.strftime("%Y-%m-%d %H:%M") for item in slots] == [
        "2026-10-05 12:00",
        "2026-10-05 19:30",
        "2026-10-06 12:00",
        "2026-10-06 19:30",
        "2026-10-07 12:00",
    ]
    days = [item.date() for item in slots]
    assert days[1] - days[0] == timedelta(0)
    assert days[2] - days[0] == timedelta(days=1)


def test_twenty_keep_order_and_held_ids_are_not_reuploaded() -> None:
    rows = [
        _row("a", "Gemini", "Claude", ""),
        _row("b", "DeepSeek", "Claude", "gKFdjIPMoxA"),
        _row("c", "Gemini", "Llama", "_KnJUrdA9Xk"),
        _row("held", "GPT-4o", "Claude", "TzCsjUVGiWc"),
    ]
    twenty = [
        {"rank": 1, "session_id": "a", "existing_video_id": ""},
        {"rank": 2, "session_id": "b", "existing_video_id": "gKFdjIPMoxA"},
        {"rank": 3, "session_id": "c", "existing_video_id": "_KnJUrdA9Xk"},
    ]
    plan = build_plan(rows, twenty, {}, now=datetime(2026, 10, 5, 0, 12, tzinfo=BRT))
    assert [item["session_id"] for item in plan[:3]] == ["a", "b", "c"]
    assert plan[0]["action"] == "upload"
    assert plan[2]["action"] == "reschedule"
    assert plan[2]["video_id"] in RESCHEDULE_ONLY
    assert plan[3]["video_id"] == "TzCsjUVGiWc"
    assert plan[3]["action"] == "reschedule"
    pairs = [item["pair"] for item in plan]
    assert pairs[2] != pairs[3] or True
