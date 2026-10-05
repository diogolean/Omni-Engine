# -*- coding: utf-8 -*-
"""Refresh script_drafts_20260825/index.json from the three rec files."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from utils.pipeline_paths import page_outputs_dir

import json

OUT = page_outputs_dir("wonder_feed") / "clips" / "script_drafts_20260825"
KEYS = (
    "ok_validator",
    "validator_reasons",
    "story",
    "story_pass",
    "spine",
)


def main() -> int:
    bundle = []
    for name, extra in (
        ("self_respect_boundaries_without_guilt.json", {}),
        ("communication_repair_after_conflict.json", {}),
        ("attachment_anxious_pursuit_cycles.json", {"dropped": True, "drop_reason": "maxims across multiple lines"}),
    ):
        path = OUT / name
        rec = json.loads(path.read_text(encoding="utf-8"))
        row = {"path": str(path), **{k: rec.get(k) for k in KEYS}, **extra}
        bundle.append(row)
        if extra.get("dropped"):
            rec["dropped"] = True
            rec["drop_reason"] = extra["drop_reason"]
            path.write_text(json.dumps(rec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    dest = OUT / "index.json"
    dest.write_text(json.dumps(bundle, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("wrote", dest)
    for row in bundle:
        print(
            Path(row["path"]).name,
            "story",
            row.get("story_pass"),
            "val",
            row.get("ok_validator"),
            "dropped" if row.get("dropped") else "",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
