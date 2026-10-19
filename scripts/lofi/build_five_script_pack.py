# -*- coding: utf-8 -*-
"""Five gate-passed scripts + Flux prompts. No image/video generation."""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.pipeline_paths import page_outputs_dir

os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from agents.writer.script_agent import (  # noqa: E402
    _CAUSAL_LINK_RE,
    assess_story_quality,
)
from core.economic_reel_lofi import config as lofi_cfg  # noqa: E402
from core.economic_reel_lofi.visual_identity import (  # noqa: E402
    act_for_index,
    apply_v2_prompts_to_lines_dev,
)
from core.economic_reel_lofi.validator_agent import validate_script  # noqa: E402

OUT = page_outputs_dir("wonder_feed") / "clips" / "script_pack_20260825"
DRAFT1 = (
    page_outputs_dir("wonder_feed")
    / "clips"
    / "script_drafts_20260825"
    / "self_respect_boundaries_without_guilt.json"
)
MAX_WORDS = int(lofi_cfg.THEMATIC_MAX_CAPTION_WORDS)
MAX_CHARS = int(lofi_cfg.THEMATIC_MAX_CAPTION_CHARS)

# Narrative job per beat (hook → takeaway). Connective is derived live.
_JOBS = (
    "hook",
    "setup",
    "complication",
    "stakes",
    "contrast",
    "cause",
    "turn",
    "insight",
    "takeaway",
)

SPECS: tuple[dict, ...] = (
    {
        "reuse_path": str(DRAFT1),
        "theme": "self_respect",
        "subtheme": "boundaries_without_guilt",
        "emotion": "self-respect / keep the no",
        "thesis": (
            "The usable move is keeping the no you already said, "
            "not explaining it until it becomes a yes."
        ),
        "world_id": "apartment_night_edge",
        "jobs": (
            "hook",
            "setup",
            "complication",
            "stakes",
            "contrast",
            "cause",
            "turn",
            "insight",
            "takeaway",
        ),
    },
    {
        "theme": "communication",
        "subtheme": "repair_after_conflict",
        "emotion": "repair / one sentence after the fight",
        "thesis": (
            "The usable move is one repair sentence after the fight, "
            "not waiting for them to guess what you meant."
        ),
        "world_id": "parked_car_night",
        "lines": (
            "I sat in the car after we fought.",
            "Because filling quiet with sorry was old.",
            "So I almost said sorry anyway.",
            "Then the empty seat held the hush.",
            "But hush was not the same as peace.",
            "When I said what I actually meant.",
            "So the empty seat stopped feeling guilt.",
            "Now the quiet isn't asking for sorry.",
            "So I stay here and say what I meant.",
        ),
        "jobs": (
            "hook",
            "cause",
            "stakes",
            "complication",
            "contrast",
            "turn",
            "insight",
            "release",
            "takeaway",
        ),
    },
    {
        "theme": "attachment",
        "subtheme": "anxious_pursuit_cycles",
        "emotion": "anxious attachment / put the third message down",
        "thesis": (
            "The usable move is putting the phone down instead of "
            "sending the third message into the silence."
        ),
        "world_id": "bedroom_night_hall",
        "lines": (
            "I wrote a third message into silence.",
            "Because the first two never came back.",
            "So I almost sent another anyway.",
            "Then I sat with it in the dark.",
            "But waiting was costing me the night.",
            "When I put the phone down, it stopped.",
            "So I left the room without checking.",
            "The silence stayed, and I stayed too.",
            "So I keep the phone down tonight.",
        ),
        "jobs": (
            "hook",
            "cause",
            "stakes",
            "complication",
            "cost",
            "turn",
            "consequence",
            "insight",
            "takeaway",
        ),
    },
    {
        "theme": "grief",
        "subtheme": "learning_to_carry_it",
        "emotion": "grief / keep the ordinary thing",
        "thesis": (
            "The usable move is leaving her mug where she left it, "
            "not packing her away to make the kitchen look finished."
        ),
        "world_id": "kitchen_night_counter",
        "lines": (
            "Her mug is still on the counter.",
            "I almost put it away this morning.",
            "But putting it away felt like losing her.",
            "So I left it, and washed my own.",
            "Because the mug still holds her morning.",
            "When I go by it, I still pause.",
            "Then the pause got shorter this week.",
            "I keep the mug where she left it.",
            "So I carry her without packing her away.",
        ),
        "jobs": (
            "hook",
            "stakes",
            "contrast",
            "consequence",
            "cause",
            "persist",
            "turn",
            "keep",
            "takeaway",
        ),
    },
    {
        "theme": "trust",
        "subtheme": "consistency_over_promises",
        "emotion": "trust / Thursday over a speech",
        "thesis": (
            "The usable move is keeping the ordinary Thursday he actually "
            "showed up for, not another forever on the steps."
        ),
        "world_id": "front_stoop_evening",
        "lines": (
            "He said forever on the front steps.",
            "But forever never made it to Thursday.",
            "So I stopped asking for a speech.",
            "Then I watched the stoop stay empty.",
            "When he sat down without a speech.",
            "Because he came back on a Thursday.",
            "I almost missed it, waiting for words.",
            "Then I held the ordinary Thursday.",
            "So I keep the Thursday, not the speech.",
        ),
        "jobs": (
            "hook",
            "contrast",
            "consequence",
            "stakes",
            "turn",
            "cause",
            "stakes",
            "hold",
            "takeaway",
        ),
    },
)


def _connective(text: str) -> str:
    m = _CAUSAL_LINK_RE.search(text or "")
    return m.group(1).lower() if m else "—"


def _check_limits(lines: tuple[str, ...] | list[str], label: str) -> None:
    for i, text in enumerate(lines, 1):
        n = len(text.split())
        c = len(text)
        if n > MAX_WORDS or c > MAX_CHARS:
            raise SystemExit(f"{label} line {i} over limit words={n} chars={c} {text!r}")


def _script_from_lines(spec: dict) -> dict:
    lines_txt = list(spec["lines"])
    _check_limits(lines_txt, f"{spec['theme']}/{spec['subtheme']}")
    rows = []
    n = len(lines_txt)
    for i, text in enumerate(lines_txt):
        rows.append(
            {
                "scene": i + 1,
                "text": text,
                "beat_text": text,
                "arc_position": act_for_index(i, n),
                "subject_type": "woman",
                "subject_expression": "sad",
                "episode_theme": spec["theme"],
                "episode_module": "relationship",
                "episode_thesis": spec["thesis"],
                "force_episode_world_id": spec["world_id"],
            }
        )
    return {
        "source_monologue": " ".join(lines_txt),
        "monologue": " ".join(lines_txt),
        "monologue_split": True,
        "direct_beats": True,
        "writer": "locked_pack",
        "lines": rows,
        "hook_type": "bold_claim",
        "theme": spec["theme"],
        "module": "relationship",
        "subtheme": spec["subtheme"],
        "thesis": spec["thesis"],
        "arc_template": "thematic_arc",
        "structure_id": "contrast_insight",
    }


def _reuse_draft1(spec: dict) -> dict:
    rec = json.loads(Path(spec["reuse_path"]).read_text(encoding="utf-8"))
    script = rec["script"]
    lines = [r for r in (script.get("lines") or []) if isinstance(r, dict)]
    texts = [str(r.get("text") or "") for r in lines]
    _check_limits(texts, "draft1")
    story = assess_story_quality(lines, theme=spec["theme"])
    if story.get("fails"):
        raise SystemExit(f"draft1 story FAIL {story['fails']}")
    return {
        "spec": spec,
        "script": script,
        "story": story,
        "reused": True,
        "ok_validator": bool(rec.get("ok_validator")),
        "validator_reasons": list(rec.get("validator_reasons") or []),
    }


def _spine_table(texts: list[str], jobs: tuple[str, ...], links: list[str]) -> list[dict]:
    rows = []
    for i, text in enumerate(texts):
        link = links[i - 1] if i else "open"
        rows.append(
            {
                "beat": i + 1,
                "line": text,
                "connective": _connective(text) if i else "—",
                "job": jobs[i] if i < len(jobs) else _JOBS[i],
                "link": link,
            }
        )
    return rows


def _pack_row(built: dict) -> dict:
    spec = built["spec"]
    script = built["script"]
    lines = [r for r in (script.get("lines") or []) if isinstance(r, dict)]
    texts = [str(r.get("text") or "") for r in lines]
    story = built["story"]
    world = script.get("episode_atmosphere") or {}
    if not world and lines:
        world = {
            "id": lines[0].get("episode_world_id"),
            "place": lines[0].get("atmosphere_place"),
            "mood": lines[0].get("atmosphere_mood"),
            "light_quality": lines[0].get("atmosphere_light"),
        }
    prompts = []
    for i, row in enumerate(lines):
        prompts.append(
            {
                "beat": i + 1,
                "line": row.get("text"),
                "angle": row.get("atmosphere_angle") or row.get("shot_scale"),
                "distance": row.get("atmosphere_distance"),
                "light": row.get("atmosphere_light") or row.get("lighting_condition"),
                "time_of_day": row.get("time_of_day"),
                "setting": row.get("setting"),
                "key_object": row.get("key_object"),
                "background": row.get("atmosphere_background"),
                "visual_prompt": row.get("visual_prompt") or "",
            }
        )
    return {
        "theme": spec["theme"],
        "subtheme": spec["subtheme"],
        "emotion": spec["emotion"],
        "thesis": spec["thesis"],
        "spoken_lines": texts,
        "story": story,
        "story_pass": not bool(story.get("fails")),
        "ok_validator": built.get("ok_validator"),
        "validator_reasons": built.get("validator_reasons") or [],
        "reused": bool(built.get("reused")),
        "visual_world": {
            "id": world.get("id"),
            "line": (
                f"{world.get('place')}. Mood: {world.get('mood')}. "
                f"Light: {world.get('light_quality')}."
            ),
            "place": world.get("place"),
            "mood": world.get("mood"),
            "light_quality": world.get("light_quality"),
        },
        "spine_table": _spine_table(
            texts, spec["jobs"], list(story.get("links") or [])
        ),
        "stills": prompts,
        "script": script,
    }


def _one_new(spec: dict) -> dict:
    script = _script_from_lines(spec)
    story = assess_story_quality(script["lines"], theme=spec["theme"])
    print(f"[{spec['theme']}] story fails={story.get('fails')} links={story.get('links')}")
    if story.get("fails"):
        raise SystemExit(f"{spec['theme']} story FAIL {story['fails']}")
    lines = script["lines"]
    apply_v2_prompts_to_lines_dev(lines, lock_visuals=False)
    if lines and isinstance(lines[0].get("episode_atmosphere"), dict):
        script["episode_atmosphere"] = dict(lines[0]["episode_atmosphere"])
    val = validate_script(
        script, module="relationship", scene_count=9, persist_on_pass=False
    )
    print(f"[{spec['theme']}] validator={val.ok} {val.reasons}")
    return {
        "spec": spec,
        "script": script,
        "story": story,
        "reused": False,
        "ok_validator": bool(val.ok),
        "validator_reasons": list(val.reasons),
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    bundle = []
    for spec in SPECS:
        print("=" * 72)
        print(spec["emotion"])
        if spec.get("reuse_path"):
            built = _reuse_draft1(spec)
        else:
            built = _one_new(spec)
        rec = _pack_row(built)
        if not rec["story_pass"]:
            raise SystemExit(f"refusing to pack failing story: {spec['theme']}")
        dest = OUT / f"{spec['theme']}_{spec['subtheme']}.json"
        dest.write_text(
            json.dumps(rec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        rec_lite = {k: rec[k] for k in rec if k != "script"}
        bundle.append({"path": str(dest), **rec_lite})
        print("wrote", dest)
        print("world", rec["visual_world"]["line"])
    index = OUT / "index.json"
    index.write_text(
        json.dumps(bundle, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print("index", index)
    if not all(r["story_pass"] for r in bundle):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
