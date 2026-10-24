# -*- coding: utf-8 -*-
"""Patch draft 1 stakes / draft 2 close. No image gen. Does not touch hope HOLD."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.pipeline_paths import page_outputs_dir

os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from agents.writer.script_agent import assess_story_quality  # noqa: E402
from core.economic_reel_lofi.validator_agent import validate_script  # noqa: E402
from core.economic_reel_lofi.visual_identity import (  # noqa: E402
    apply_v2_prompts_to_lines_dev,
)

OUT = page_outputs_dir("wonder_feed") / "clips" / "script_drafts_20260825"
D1 = OUT / "self_respect_boundaries_without_guilt.json"
D2 = OUT / "communication_repair_after_conflict.json"
LINE4 = "Then I almost lost the no in the draft."
LINE9 = "So I stay here and say what I meant."


def _wc(text: str) -> tuple[int, int]:
    return len(text.split()), len(text)


def _patch_lines(script: dict, updates: dict[int, str]) -> dict:
    lines = script["lines"]
    for i, text in updates.items():
        lines[i]["text"] = text
        lines[i]["beat_text"] = text
    script["monologue"] = " ".join(str(r.get("text") or "") for r in lines)
    script["source_monologue"] = script["monologue"]
    script["hook_type"] = "bold_claim"
    return script


def _spine(script: dict) -> dict:
    lines = [r for r in (script.get("lines") or []) if isinstance(r, dict)]
    return {
        "theme": script.get("theme"),
        "subtheme": script.get("subtheme"),
        "thesis": script.get("thesis"),
        "structure_id": script.get("structure_id"),
        "spoken_lines": [r.get("text") for r in lines],
        "episode_atmosphere": script.get("episode_atmosphere"),
        "beats": [
            {
                "beat": i + 1,
                "line": r.get("text"),
                "setting": r.get("setting"),
                "key_object": r.get("key_object"),
                "angle": r.get("atmosphere_angle") or r.get("shot_scale"),
                "distance": r.get("atmosphere_distance"),
                "light_direction": r.get("atmosphere_light"),
                "world": r.get("atmosphere_place"),
                "background": r.get("atmosphere_background"),
            }
            for i, r in enumerate(lines)
        ],
    }


def _apply_atmosphere(script: dict) -> None:
    lines = script["lines"]
    if lines and isinstance(lines[0], dict):
        lines[0]["episode_theme"] = script.get("theme") or ""
        lines[0]["episode_module"] = "relationship"
        lines[0]["episode_thesis"] = script.get("thesis") or ""
    apply_v2_prompts_to_lines_dev(lines, lock_visuals=False)
    if lines and isinstance(lines[0], dict) and isinstance(
        lines[0].get("episode_atmosphere"), dict
    ):
        script["episode_atmosphere"] = dict(lines[0]["episode_atmosphere"])


def main() -> int:
    print("d1 line4", _wc(LINE4), LINE4)
    print("d2 line9", _wc(LINE9), LINE9)

    rec1 = json.loads(D1.read_text(encoding="utf-8"))
    script1 = _patch_lines(rec1["script"], {3: LINE4})
    story1 = assess_story_quality(script1["lines"], theme=script1.get("theme") or "")
    print("D1 STORY", story1)

    rec2 = json.loads(D2.read_text(encoding="utf-8"))
    script2 = _patch_lines(rec2["script"], {8: LINE9})
    story2 = assess_story_quality(script2["lines"], theme=script2.get("theme") or "")
    print("D2 STORY", story2)

    if not story1.get("fails"):
        chosen, label, rec, path, story = script1, "draft1", rec1, D1, story1
    elif not story2.get("fails"):
        chosen, label, rec, path, story = script2, "draft2", rec2, D2, story2
    else:
        print("NEITHER STORY PASS")
        rec1["story"] = story1
        rec1["story_pass"] = False
        rec1["script"] = script1
        rec1["spine"] = _spine(script1)
        D1.write_text(json.dumps(rec1, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        rec2["story"] = story2
        rec2["story_pass"] = False
        rec2["script"] = script2
        rec2["spine"] = _spine(script2)
        D2.write_text(json.dumps(rec2, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return 1

    _apply_atmosphere(chosen)
    val = validate_script(
        chosen, module="relationship", scene_count=9, persist_on_pass=False
    )
    print("VALIDATOR", val.ok, val.reasons)
    rec["script"] = chosen
    rec["story"] = story
    rec["story_pass"] = not bool(story.get("fails"))
    rec["ok_validator"] = bool(val.ok)
    rec["validator_reasons"] = list(val.reasons)
    rec["spine"] = _spine(chosen)
    path.write_text(json.dumps(rec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("WROTE", path, "label", label)
    print("WORLD", chosen.get("episode_atmosphere"))
    for beat in rec["spine"]["beats"]:
        print(
            f"  {beat['beat']} obj={beat['key_object']!r} set={beat['setting']!r}"
        )

    if chosen is not script2:
        rec2["script"] = script2
        rec2["story"] = story2
        rec2["story_pass"] = not bool(story2.get("fails"))
        rec2["spine"] = _spine(script2)
        D2.write_text(
            json.dumps(rec2, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print("WROTE d2 text patch")
    if chosen is not script1:
        rec1["script"] = script1
        rec1["story"] = story1
        rec1["story_pass"] = not bool(story1.get("fails"))
        rec1["spine"] = _spine(script1)
        D1.write_text(
            json.dumps(rec1, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print("WROTE d1 text patch")
    return 0 if val.ok or rec["story_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
