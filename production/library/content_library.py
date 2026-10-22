# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def recent_topics(path: Path, last_n: int = 40) -> list[str]:
    """Return the most recent ``topic`` strings from ``content_library.json``."""
    rows = load_library(path)
    out: list[str] = []
    for row in rows[-max(1, int(last_n)):]:
        if not isinstance(row, dict):
            continue
        topic = str(row.get("topic") or "").strip()
        if topic:
            out.append(topic)
    return out


def load_library(path: Path) -> list[dict[str, Any]]:
    from shared.store import hydrate_state_file

    path = hydrate_state_file(Path(path))
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else []


def save_library(path: Path, rows: list[dict[str, Any]]) -> None:
    from shared.store import sync_state_file

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    sync_state_file(path)


def append_entry(path: Path, entry: dict[str, Any]) -> dict[str, Any]:
    """Append ``entry`` verbatim."""
    rows = load_library(path)
    rows.append(entry)
    save_library(path, rows)
    return entry


def build_library_metadata(
    *,
    topic: str,
    final_caption: str,
    imgbb_url: str,
    video_path: str = "",
    carousel_images: "list[dict[str, str]] | None" = None,
    channel_id: str = "",
    post_type: str = "",
    title: str = "",
) -> dict[str, Any]:
    """ContentItem v1 row with the lean aliases older readers still use.

    ``carousel_images`` (optional) is a list of per-slide references for
    CAROUSEL posts, so the library entry carries every image of the post and
    not just the first one.
    """
    from shared.contracts import new_content_item

    item = new_content_item(
        topic=topic,
        final_caption=final_caption,
        imgbb_url=imgbb_url,
        video_path=video_path,
        carousel_images=list(carousel_images) if carousel_images else None,
        channel_id=channel_id,
        post_type=post_type,
        title=title,
        timestamp=_utc_now_iso(),
    )
    return item.to_library_row()


def dump_raw_research_to_log(
    logs_dir: Path,
    *,
    run_stamp: str,
    topic: str,
    variant_index: int,
    raw_fact_sheet: str,
) -> Path:
    """
    Write the full researcher fact sheet to ``logs/research_<run_stamp>_v<n>.txt``
    so it never bloats the lean library JSON.
    """
    logs_dir = Path(logs_dir).expanduser().resolve()
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"research_{run_stamp}_v{variant_index:02d}.txt"
    header = f"Topic: {topic}\nVariant: {variant_index}\nTimestamp: {_utc_now_iso()}\n\n"
    log_path.write_text(header + (raw_fact_sheet or ""), encoding="utf-8")
    return log_path
