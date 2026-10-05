# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path as _ReorgPath
import sys as _reorg_sys
_REORG_ROOT = _ReorgPath(__file__).resolve().parents[2]
if str(_REORG_ROOT) not in _reorg_sys.path:
    _reorg_sys.path.insert(0, str(_REORG_ROOT))

from utils.pipeline_paths import coerce_outputs_path

import json
from pathlib import Path

STORE = Path("core/economic_reel_lofi/store")
HOLDS = {
    "regret": coerce_outputs_path("outputs/wonder_feed/clips/lofi_hold_regret_20260823_203537_v01.json"),
    "belonging": coerce_outputs_path("outputs/wonder_feed/clips/lofi_hold_belonging_20260823_203846_v02.json"),
}


def main() -> None:
    for theme, hold_path in HOLDS.items():
        hold = json.loads(hold_path.read_text(encoding="utf-8"))
        script = hold["script"]
        v1 = json.loads((STORE / f"locked_script_{theme}_structure_v1.json").read_text(encoding="utf-8"))
        caps = [str(r.get("text") or "") for r in v1["lines"]]
        for row, text in zip(script["lines"], caps):
            row["text"] = text
            row["beat_text"] = text
        script["monologue"] = " ".join(caps)
        work = hold.get("work_dir")
        images = hold.get("scene_images") or [f"{work}/scene_{i:02d}.png" for i in range(1, 10)]
        out = {
            "manual_accept_scenes": list(range(1, 10)),
            "work_dir": work,
            "scene_images": images,
            "script": script,
        }
        dest = STORE / f"locked_script_{theme}_structure_v1_assemble.json"
        dest.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {dest.name} work={work}")


if __name__ == "__main__":
    main()
