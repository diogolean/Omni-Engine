# -*- coding: utf-8 -*-
"""Text-only tool-script drafts. No image/video. Does not touch the hope HOLD."""
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

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=True, encoding="utf-8-sig")

from agents.writer.script_agent import (  # noqa: E402
    assess_story_quality,
    generate_script,
    repair_script_captions,
)
from core.economic_reel_lofi import lofi_collections as rag  # noqa: E402
from core.economic_reel_lofi.validator_agent import validate_script  # noqa: E402
from core.economic_reel_lofi.visual_identity import preferred_concrete_noun  # noqa: E402

OUT = page_outputs_dir("wonder_feed") / "clips" / "script_drafts_20260825"
MODULE = "relationship"
SCENE_COUNT = 9

# Different emotion + a concrete tool the viewer can actually do.
DRAFTS: tuple[dict[str, str], ...] = (
    {
        "theme": "self_respect",
        "subtheme": "boundaries_without_guilt",
        "thesis": (
            "The usable move is keeping the no you already said, "
            "not explaining it until it becomes a yes."
        ),
    },
    {
        "theme": "communication",
        "subtheme": "repair_after_conflict",
        "thesis": (
            "The usable move is one repair sentence after the fight, "
            "not waiting for them to guess what you meant."
        ),
    },
    {
        "theme": "attachment",
        "subtheme": "anxious_pursuit_cycles",
        "thesis": (
            "The usable move is putting the phone down instead of "
            "sending the third message into the silence."
        ),
    },
)

_ANGLES = ("wide", "medium", "medium-close", "extreme-close")
_DIST = ("figure small in frame", "full-figure", "object as subject", "glow only")
_LIGHT = (
    "hard warm from the left",
    "streetlight behind",
    "warm light from inside a pane",
    "object lights itself",
    "side light, shadow on the wall",
)


def _licensed(text: str) -> list[str]:
    noun = preferred_concrete_noun(text)
    return [noun] if noun else ["figure"]


def _spine(script: dict) -> dict:
    lines = [r for r in (script.get("lines") or []) if isinstance(r, dict)]
    beats = []
    for i, row in enumerate(lines):
        text = str(row.get("text") or "")
        beats.append(
            {
                "beat": i + 1,
                "line": text,
                "licensed_nouns": _licensed(text),
                "setting": row.get("setting") or "",
                "key_object": row.get("key_object") or "",
                "angle": _ANGLES[i % len(_ANGLES)],
                "distance": _DIST[i % len(_DIST)],
                "light_direction": _LIGHT[i % len(_LIGHT)],
            }
        )
    return {
        "theme": script.get("theme"),
        "subtheme": script.get("subtheme"),
        "thesis": script.get("thesis") or script.get("locked_thesis") or "",
        "structure_id": script.get("structure_id") or script.get("arc_template") or "",
        "spoken_lines": [b["line"] for b in beats],
        "beats": beats,
    }


def _one(spec: dict[str, str]) -> dict:
    theme_row = rag.select_theme(
        MODULE, theme=spec["theme"], subtheme=spec["subtheme"]
    )
    script = generate_script(
        module=MODULE,
        theme=spec["theme"],
        subtheme=spec["subtheme"],
        scene_count=SCENE_COUNT,
        theme_row=theme_row,
        lock_thesis=spec["thesis"],
    )
    script["subtheme"] = spec["subtheme"]
    repair_script_captions(script)
    result = validate_script(
        script, module=MODULE, scene_count=SCENE_COUNT, persist_on_pass=False
    )
    story = assess_story_quality(
        (result.script or script).get("lines") or script.get("lines") or [],
        theme=spec["theme"],
    )
    chosen = result.script or script
    rec = {
        "ok_validator": bool(result.ok),
        "validator_reasons": list(result.reasons),
        "story": story,
        "story_pass": not bool(story.get("fails")),
        "script": chosen,
        "spine": _spine(chosen),
    }
    return rec


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    bundle: list[dict] = []
    for spec in DRAFTS:
        print("=" * 72)
        print(f"DRAFT {spec['theme']} / {spec['subtheme']}")
        print(f"thesis: {spec['thesis']}")
        rec = _one(spec)
        dest = OUT / f"{spec['theme']}_{spec['subtheme']}.json"
        dest.write_text(
            json.dumps(rec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        bundle.append({"path": str(dest), **{k: rec[k] for k in rec if k != "script"}})
        print(f"validator={'PASS' if rec['ok_validator'] else 'FAIL'} {rec['validator_reasons']}")
        print(f"story={'PASS' if rec['story_pass'] else 'FAIL'} {rec['story'].get('fails')}")
        print("-" * 72)
        for i, line in enumerate(rec["spine"]["spoken_lines"], 1):
            nouns = rec["spine"]["beats"][i - 1]["licensed_nouns"]
            print(f"{i}. {line}  [{', '.join(nouns)}]")
        print(f"wrote {dest}")
    index = OUT / "index.json"
    index.write_text(json.dumps(bundle, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("index", index)
    return 0 if all(r.get("story_pass") for r in bundle) else 1


if __name__ == "__main__":
    raise SystemExit(main())
