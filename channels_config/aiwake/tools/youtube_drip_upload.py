"""Upload a few Aiwake videos per day. Private, scheduled, resumable.

One run uploads at most ``--max`` videos (2-4, default 3) and stops on the
upload quota. ``--dry-run`` only prints the next picks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from channels_config.aiwake.avatars import model_family  # noqa: E402
from channels_config.aiwake.tools.production_status import is_publishable  # noqa: E402
from modules.distribution_contract import load_distribution_library  # noqa: E402

LIBRARY = ROOT / "channels_config" / "aiwake" / "store" / "content_library.json"
LEDGER = ROOT / "outputs" / "aiwake" / "upload_ledger.json"
BRT = timezone(timedelta(hours=-3))
# Slots are 12:00 and 19:00 BRT, and nothing before 09/10 12:00 (the first slot after 08/10 19:00).
FIRST = datetime(2026, 10, 9, 12, 0, tzinfo=BRT)
SEED = 20261001


def _pair(row: dict[str, Any]) -> tuple[str, str]:
    spoken = [item for item in (row.get("spoken_utterances") or []) if isinstance(item, dict)]
    left = next((model_family(str(item.get("speaker") or "")) for item in spoken if item.get("role") == "orchestrator"), "")
    right = next((model_family(str(item.get("speaker") or "")) for item in spoken if item.get("role") == "target"), "")
    return left, right


def _approved(row: dict[str, Any]) -> bool:
    qa = row.get("caption_qa") if isinstance(row.get("caption_qa"), dict) else {}
    return str(qa.get("approval") or "") == "approved"


def eligible_rows(rows: list[dict[str, Any]], *, require_approval: bool = True) -> list[dict[str, Any]]:
    picked = []
    for row in rows:
        if str(row.get("production_scope") or "") != "in":
            continue
        if not is_publishable(row):
            continue
        if require_approval and not _approved(row):
            continue
        yt = ((row.get("platform_overrides") or {}).get("youtube") or {})
        if str(yt.get("video_id") or "").strip():
            continue
        video = Path(str(row.get("video_path") or ""))
        if not video.is_file():
            continue
        picked.append(row)
    return picked


def order_rows(rows: list[dict[str, Any]], *, seed: int = SEED) -> list[dict[str, Any]]:
    """Shuffle, then avoid the same pair twice in a row and prefer rarer pairs."""
    rng = random.Random(seed)
    pool = list(rows)
    rng.shuffle(pool)
    position = {id(row): index for index, row in enumerate(pool)}
    counts: dict[tuple[str, str], int] = {}
    ordered: list[dict[str, Any]] = []
    while pool:
        last = _pair(ordered[-1]) if ordered else ("", "")

        def rank(row: dict[str, Any]) -> tuple:
            pair = _pair(row)
            return (counts.get(pair, 0), pair == last, position[id(row)])

        pool.sort(key=rank)
        nxt = pool.pop(0)
        ordered.append(nxt)
        pair = _pair(nxt)
        counts[pair] = counts.get(pair, 0) + 1
    return ordered


def _slots(start: datetime, count: int) -> list[datetime]:
    cursor = start
    found = []
    while len(found) < count:
        if cursor.hour in {12, 19} and cursor.minute == 0:
            found.append(cursor)
        cursor += timedelta(hours=1)
    return found


def _taken_slots(rows: list[dict[str, Any]], ledger: dict[str, Any]) -> set[str]:
    taken = set()
    for row in rows:
        yt = ((row.get("platform_overrides") or {}).get("youtube") or {})
        raw = str(yt.get("scheduled_time") or "")
        if raw:
            taken.add(raw)
    for item in ledger.get("uploads") or []:
        raw = str(item.get("publish_at") or "")
        if raw:
            taken.add(raw)
    return taken


def next_slot(rows: list[dict[str, Any]], ledger: dict[str, Any]) -> datetime:
    """Earliest 12:00 or 19:00 BRT after 8 Oct that no upload already holds."""
    taken = _taken_slots(rows, ledger)
    cursor = FIRST
    for _ in range(400):
        stamp = cursor.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if stamp not in taken:
            return cursor
        cursor = _slots(cursor + timedelta(hours=1), 1)[0]
    return cursor


def file_md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def plan(
    rows: list[dict[str, Any]],
    *,
    limit: int,
    seed: int = SEED,
    require_approval: bool = True,
) -> list[dict[str, Any]]:
    ledger = json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.is_file() else {"uploads": []}
    done = {str(item.get("session_id") or "") for item in ledger.get("uploads") or []}
    titles = set()
    queue = []
    slot = next_slot(rows, ledger)
    for row in order_rows(eligible_rows(rows, require_approval=require_approval), seed=seed):
        sid = str(row.get("session_id") or "")
        if sid in done:
            continue
        video = Path(str(row.get("video_path") or ""))
        title = str(((row.get("platform_overrides") or {}).get("youtube") or {}).get("title") or "")
        if title.strip().lower() in titles:
            continue
        titles.add(title.strip().lower())
        left, right = _pair(row)
        queue.append(
            {
                "session_id": sid,
                "pair": f"{left} vs {right}",
                "slot_brt": slot.strftime("%Y-%m-%d %H:%M"),
                "title": title,
                "file": video.name,
            }
        )
        slot = _slots(slot + timedelta(hours=1), 1)[0]
        if len(queue) >= limit:
            break
    return queue


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--preview", type=int, default=10)
    args = parser.parse_args()
    if args.max < 2 or args.max > 4:
        raise SystemExit("--max must be 2, 3, or 4")
    rows = load_distribution_library(LIBRARY)
    if args.dry_run:
        approved = plan(rows, limit=args.preview)
        waiting = plan(rows, limit=args.preview, require_approval=False)
        print(
            json.dumps(
                {
                    "eligible_approved": len(eligible_rows(rows)),
                    "guard_pass_pending_approval": len(eligible_rows(rows, require_approval=False)),
                    "preview_approved": approved,
                    "preview_after_approval": waiting,
                    "seed": SEED,
                },
                indent=2,
            )
        )
        return 0
    raise SystemExit("refusing a live upload from this run; pass --dry-run")


if __name__ == "__main__":
    raise SystemExit(main())
