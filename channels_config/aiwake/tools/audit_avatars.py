"""Audit every library video. The face on each sampled turn must match the jsonl model."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from channels_config.aiwake.avatars import model_family, require_pairing  # noqa: E402
from channels_config.aiwake.tools.avatar_guard import (  # noqa: E402
    _frame_at,
    _jsonl,
    _turn_wavs,
    _wav_duration,
    nameplate_family,
)
from modules.distribution_contract import load_distribution_library  # noqa: E402

LIBRARY = ROOT / "channels_config" / "aiwake" / "store" / "content_library.json"
TRANSCRIPTS = ROOT / "channels_config" / "aiwake" / "store" / "transcripts"
OUT = ROOT / "outputs" / "aiwake" / "avatar_audit.json"


def _fix(session_turns: list[dict[str, Any]], mismatched: bool) -> str:
    if not session_turns:
        return "unknown"
    left = next((t for t in session_turns if str(t.get("role")) == "orchestrator"), None)
    right = next((t for t in session_turns if str(t.get("role")) == "target"), None)
    if not left or not right:
        return "unknown"
    try:
        require_pairing(str(left.get("model_slug") or ""), str(right.get("model_slug") or ""))
    except ValueError:
        return "regenerated"
    return "avatar_swap" if mismatched else "ok"


def main() -> int:
    rows = load_distribution_library(LIBRARY)
    results = []
    for index, row in enumerate(rows):
        video = Path(str(row.get("video_path") or ""))
        sid = str(row.get("session_id") or "")
        if not video.is_file() or video.suffix.lower() != ".mp4":
            continue
        if video.parent.name != "animation_clips":
            continue
        turns = _jsonl(sid, TRANSCRIPTS)
        wavs = _turn_wavs(video, sid)
        durations = []
        for wav in wavs:
            try:
                durations.append(_wav_duration(wav))
            except (OSError, Exception):
                durations.append(3.0)
        cursor = 0.4
        frames = []
        mismatch = False
        # One frame for each seat: first orchestrator and first target.
        wanted = []
        for turn_index, turn in enumerate(turns):
            role = str(turn.get("role") or "")
            if role in {"orchestrator", "target"} and role not in {item[1] for item in wanted}:
                wanted.append((turn_index, role, turn))
        for turn_index, _role, turn in wanted:
            span = durations[turn_index] if turn_index < len(durations) else 3.0
            # Walk the clock up to this turn.
            moment = 0.4
            for earlier in range(turn_index):
                moment += (durations[earlier] if earlier < len(durations) else 3.0) + 0.4
            moment += max(0.4, span * 0.45)
            frame = _frame_at(video, moment, OUT.parent / "guard_frames" / f"{sid}_{turn_index}.jpg")
            expected = model_family(str(turn.get("model_slug") or turn.get("speaker_name") or ""))
            seen = nameplate_family(frame) if frame is not None else ""
            if seen != expected:
                mismatch = True
            frames.append({"turn": turn_index, "expected": expected, "seen": seen})
            cursor = moment
        review = row.get("quality_review") if isinstance(row.get("quality_review"), dict) else {}
        silence = "silence" in (review.get("reasons") or [])
        results.append(
            {
                "session_id": sid,
                "video": video.name,
                "mismatch": mismatch,
                "silence": silence,
                "fix": _fix(turns, mismatch or silence),
                "frames": frames,
            }
        )
        if index % 15 == 0:
            print(f"audited {len(results)}", flush=True)
    payload = {
        "at": datetime.now(timezone.utc).isoformat(),
        "videos": len(results),
        "mismatches": sum(1 for item in results if item["mismatch"]),
        "avatar_swap": sum(1 for item in results if item["fix"] == "avatar_swap"),
        "regenerated": sum(1 for item in results if item["fix"] == "regenerated"),
        "rows": results,
    }
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps({k: payload[k] for k in ("videos", "mismatches", "avatar_swap", "regenerated")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
