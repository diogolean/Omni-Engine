# -*- coding: utf-8 -*-
"""Export approved tightened scripts as locked stills inputs with hook stamps."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "outputs/wonder_feed/clips/freeform_drafts"

HOOKS = {
    "forgiveness": {
        "src": "tightened_locked_02_forgiveness.json",
        "out": "prod_02_forgiveness.json",
        "angle": "left-facing profile",
        "gaze": "gaze toward the light",
        "palette_key": "CONTRAST",
        "duration_s": 24.0,
    },
    "starting_over": {
        "src": "tightened_locked_03_starting_over.json",
        "out": "prod_03_starting_over.json",
        "angle": "right-facing profile",
        "gaze": "gaze out of frame",
        "palette_key": "CONTRAST",
        "duration_s": 24.0,
    },
    "suffer_imagination": {
        "src": "tightened_locked_04_suffer_imagination.json",
        "out": "prod_04_suffer_imagination.json",
        "angle": "right-facing profile",
        "gaze": "gaze out of frame",
        "palette_key": "COLD",
        "duration_s": 30.0,
    },
}

STRIP = (
    "visual_concept",
    "scene_description",
    "visual_prompt",
    "concept_locked",
)


def main() -> None:
    for pack, spec in HOOKS.items():
        blob = json.loads((OUT / spec["src"]).read_text(encoding="utf-8"))
        script = dict(blob.get("script") or blob)
        n = len([r for r in (script.get("lines") or []) if isinstance(r, dict)])
        script["duration_requested_s"] = float(spec["duration_s"])
        script["scene_duration_s"] = 3.0
        script["hook_variety"] = {
            "angle": spec["angle"],
            "gaze": spec["gaze"],
            "palette_key": spec["palette_key"],
        }
        for i, row in enumerate(script.get("lines") or []):
            if not isinstance(row, dict):
                continue
            for k in STRIP:
                row.pop(k, None)
            row["duration_s"] = 3.0
            if i == 0:
                row["hook_angle"] = spec["angle"]
                row["hook_gaze"] = spec["gaze"]
                row["palette_key"] = spec["palette_key"]
        dest = OUT / spec["out"]
        dest.write_text(
            json.dumps({"script": script}, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"{pack}: {n} beats → {dest.name}")


if __name__ == "__main__":
    main()
