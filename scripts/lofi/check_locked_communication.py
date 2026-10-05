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
from core.economic_reel_lofi.setting_archetypes import classify_setting_archetype

path = Path("core/economic_reel_lofi/store/locked_script_communication_v1.json")
data = json.loads(path.read_text(encoding="utf-8"))
for row in data["lines"]:
    row["setting_archetype"] = classify_setting_archetype(
        row.get("setting", ""), row.get("key_object", "")
    )
    print(
        f"  {row['scene']} {row['beat_function']:12} {row['setting_archetype']:20} "
        f"{row['text']!r}"
    )
reasons = _narrative_gate_reasons(data, "relationship")
print("HOLDS:", reasons or "none")
print("principle", json.dumps(data.get("principle_voice"), indent=2))
print("agency", json.dumps(data.get("insight_agency"), indent=2))
print("settings", json.dumps(data.get("setting_archetypes"), indent=2))
print("cross", json.dumps(data.get("setting_archetype_cross"), indent=2))
