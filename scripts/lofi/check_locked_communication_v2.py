# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path as _ReorgPath
import sys as _reorg_sys
_REORG_ROOT = _ReorgPath(__file__).resolve().parents[2]
if str(_REORG_ROOT) not in _reorg_sys.path:
    _reorg_sys.path.insert(0, str(_REORG_ROOT))

import json
from pathlib import Path

from agents.writer.script_agent import _narrative_gate_reasons
from core.economic_reel_lofi.setting_archetypes import (
    classify_composition_type,
    classify_setting_archetype,
    score_composition_types,
)

path = Path("core/economic_reel_lofi/store/locked_script_communication_v2.json")
data = json.loads(path.read_text(encoding="utf-8"))
for row in data["lines"]:
    row["setting_archetype"] = classify_setting_archetype(
        row.get("setting", ""), row.get("key_object", "")
    )
    row["composition_type"] = classify_composition_type(row)
    print(
        f"  {row['scene']} {row['beat_function']:12} "
        f"{row['composition_type']:18} {row['setting_archetype']:20} "
        f"{row['text']!r}"
    )
print("composition", score_composition_types(data["lines"]))
reasons = _narrative_gate_reasons(data, "relationship")
print("HOLDS:", reasons or "none")
