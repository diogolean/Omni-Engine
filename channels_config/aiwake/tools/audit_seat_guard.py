"""Re-audit every animation clip with the nameplate and seat guard.

Writes ``outputs/aiwake/seat_guard_audit.json``. With ``--stamp``, copies each
transcript, then stores ``render_manifest`` on the library row and the
transcript metadata. A failing current video is not publishable.
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from channels_config.aiwake.tools.avatar_guard import guard_video  # noqa: E402
from channels_config.aiwake.tools.repair_captions import _backup  # noqa: E402
from modules.distribution_contract import load_distribution_library, save_distribution_library  # noqa: E402

LIBRARY = ROOT / "channels_config" / "aiwake" / "store" / "content_library.json"
TRANSCRIPTS = ROOT / "channels_config" / "aiwake" / "store" / "transcripts"
OUT = ROOT / "outputs" / "aiwake" / "seat_guard_audit.json"


def _clips(rows: list[dict[str, Any]]) -> Path:
    for row in rows:
        raw = str(row.get("video_path") or "")
        if "animation_clips" in raw.replace("\\", "/").lower():
            return Path(raw).parent
    raise SystemExit("animation_clips directory not found on a library row")


def _index(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for row in rows:
        for key in ("video_path", "superseded_video_path"):
            raw = str(row.get(key) or "").strip()
            if raw:
                found[str(Path(raw)).lower()] = row
    return found


def _audit_file(path: Path, row: dict[str, Any] | None) -> dict[str, Any]:
    session_id = str((row or {}).get("session_id") or "")
    started = time.perf_counter()
    if not session_id or not (TRANSCRIPTS / f"{session_id}.jsonl").is_file():
        return {
            "file": path.name,
            "session_id": session_id,
            "status": "unmatched",
            "reasons": ["jsonl_missing"],
            "seconds": 0,
        }
    result = guard_video(path, session_id, transcripts=TRANSCRIPTS)
    return {
        "file": path.name,
        "path": str(path),
        "session_id": session_id,
        "current": str((row or {}).get("video_path") or "").lower() == str(path).lower(),
        "status": result.get("status"),
        "reasons": result.get("reasons") or [],
        "seconds": round(time.perf_counter() - started, 1),
        "render_manifest": result.get("render_manifest") or {},
        "frames": [
            {key: value for key, value in frame.items() if key != "frame"}
            for frame in (result.get("frames") or [])
        ],
    }


def _persist_transcript(session_id: str, manifest: dict[str, Any], stamp: str) -> str:
    path = TRANSCRIPTS / f"{session_id}.json"
    if not path.is_file() or not manifest:
        return "missing"
    backup = TRANSCRIPTS / "backups" / f"{path.stem}.{stamp}.json"
    backup.parent.mkdir(parents=True, exist_ok=True)
    if not backup.is_file():
        shutil.copy2(path, backup)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return "not_object"
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    metadata["render_manifest"] = manifest
    payload["metadata"] = metadata
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return "written"


def stamp(report: dict[str, Any]) -> dict[str, Any]:
    backup = _backup(LIBRARY)
    rows = load_distribution_library(LIBRARY)
    before = len(rows)
    by_session: dict[str, dict[str, Any]] = {}
    for item in report["files"]:
        if item.get("current") and item.get("session_id"):
            by_session[item["session_id"]] = item
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    stamped = {"pass": 0, "fail": 0, "transcripts": 0}
    for row in rows:
        sid = str(row.get("session_id") or "")
        item = by_session.get(sid)
        if item is None or item.get("status") not in {"pass", "fail"}:
            continue
        manifest = item.get("render_manifest") or {}
        if manifest:
            row["render_manifest"] = manifest
            if _persist_transcript(sid, manifest, report["stamp"]) == "written":
                stamped["transcripts"] += 1
        review = dict(row.get("quality_review") or {})
        review["guard"] = item["status"]
        review["status"] = item["status"]
        review["reasons"] = item.get("reasons") or []
        review["checked_at"] = now
        row["quality_review"] = review
        stamped[item["status"]] += 1
    if len(rows) != before:
        raise RuntimeError("library count changed")
    save_distribution_library(LIBRARY, rows)
    return {"backup": backup.name, "rows": len(rows), **stamped}


def main() -> int:
    if "--stamp-only" in sys.argv:
        report = json.loads(OUT.read_text(encoding="utf-8"))
        print(json.dumps(stamp(report)))
        return 0
    rows = load_distribution_library(LIBRARY)
    folder = _clips(rows)
    indexed = _index(rows)
    files = sorted(folder.glob("*.mp4"))
    started = time.perf_counter()
    audited = []
    for index, path in enumerate(files, start=1):
        row = indexed.get(str(path).lower())
        item = _audit_file(path, row)
        audited.append(item)
        print(f"{index}/{len(files)} {item['status']} {item['file']} {item.get('reasons')}", flush=True)
    counts = {"pass": 0, "fail": 0, "unmatched": 0}
    for item in audited:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    report = {
        "stamp": datetime.now().strftime("%Y%m%d-%H%M%S"),
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "folder": str(folder),
        "seconds": round(time.perf_counter() - started, 1),
        "counts": counts,
        "files": audited,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(OUT), **counts, "seconds": report["seconds"]}), flush=True)
    if "--stamp" in sys.argv:
        print(json.dumps(stamp(report)), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
