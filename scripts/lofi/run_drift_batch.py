# -*- coding: utf-8 -*-
"""Run 5 off-domain pattern stress tests (script-only, then assemble)."""
from __future__ import annotations

from pathlib import Path as _ReorgPath
import sys as _reorg_sys
_REORG_ROOT = _ReorgPath(__file__).resolve().parents[2]
if str(_REORG_ROOT) not in _reorg_sys.path:
    _reorg_sys.path.insert(0, str(_REORG_ROOT))

# Assignments: pattern (source domain) -> unrelated theme
PAIRS = [
    ("perseverance", "quiet_proof_inventory", "trust"),
    ("belonging", "personification_sensory", "grief"),
    ("communication", "learning_to_hold_it", "grief/healing"),
    ("vulnerability", "setting_the_weight_down", "forgiveness"),
    ("regret", "open_question", "loneliness"),
]
