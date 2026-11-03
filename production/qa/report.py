# -*- coding: utf-8 -*-
"""Write the per-reel QA report beside the MP4 and under outputs/<page>/qa/."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def report_targets(
    *,
    video_path: Path | str | None,
    output_dir: Path | str | None,
    stem: str,
) -> list[Path]:
    targets: list[Path] = []
    if video_path:
        video = Path(video_path)
        targets.append(video.with_name(video.stem + ".qa.json"))
    if output_dir:
        targets.append(Path(output_dir) / f"{stem or 'reel'}.qa.json")
    unique: list[Path] = []
    seen: set[str] = set()
    for path in targets:
        key = str(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def write_qa_report(report: dict[str, Any], targets: list[Path]) -> str:
    payload = json.dumps(report, indent=2, ensure_ascii=False)
    written: list[str] = []
    for path in targets:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")
        written.append(str(path))
    report["report_paths"] = written
    report["report_path"] = written[0] if written else ""
    return report["report_path"]


def new_report(
    *,
    post_type: str,
    video_path: str,
    beats: list[dict[str, Any]],
    mp4: dict[str, Any],
    verdict: str,
    blocked_reason: str,
    channel: str = "",
) -> dict[str, Any]:
    return {
        "verdict": verdict,
        "publishable": verdict == "pass",
        "post_type": post_type,
        "channel": channel,
        "video_path": video_path,
        "beats": beats,
        "mp4": mp4,
        "blocked_reason": blocked_reason,
        "created_utc": datetime.now(timezone.utc).isoformat(),
    }
