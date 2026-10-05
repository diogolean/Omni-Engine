# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path as _ReorgPath
import sys as _reorg_sys
_REORG_ROOT = _ReorgPath(__file__).resolve().parents[2]
if str(_REORG_ROOT) not in _reorg_sys.path:
    _reorg_sys.path.insert(0, str(_REORG_ROOT))

import json
from pathlib import Path

hold_path = Path(
    "outputs/wonder_feed/clips/lofi_hold_communication_20260823_041455_v01.json"
)
hold = json.loads(hold_path.read_text(encoding="utf-8"))
script = hold["script"]
work = "outputs/wonder_feed/assets/lofi_run_20260823_041455_01"
images = [f"{work}/scene_{i:02d}.png" for i in range(1, 10)]
# Scene 2 chairs still is a known object-gate false positive (same family as bench).
# Scene 6 bench was already accepted on the prior communication pass.
out = {
    "manual_accept_scenes": [2, 6],
    "work_dir": work,
    "scene_images": images,
    "script": script,
}
dest = Path(
    "core/economic_reel_lofi/store/locked_script_communication_v2_assemble.json"
)
dest.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"wrote {dest} accepts={out['manual_accept_scenes']}")
for i, row in enumerate(script["lines"], 1):
    gate = row.get("default_object_gate") or {}
    print(
        f"  {i} image_ok={gate.get('image_ok')} "
        f"comp={row.get('composition_type')} "
        f"close={row.get('close_variant') or '-'}"
    )
