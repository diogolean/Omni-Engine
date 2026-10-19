# -*- coding: utf-8 -*-
"""Restamp Flux prompts on the 20260825 pack. Spoken lines stay frozen."""
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

from core.economic_reel_lofi.visual_identity import (  # noqa: E402
    apply_v2_prompts_to_lines_dev,
)

PACK = page_outputs_dir("wonder_feed") / "clips" / "script_pack_20260825"
PACK_FILES = (
    "self_respect_boundaries_without_guilt.json",
    "communication_repair_after_conflict.json",
    "attachment_anxious_pursuit_cycles.json",
    "grief_learning_to_carry_it.json",
    "trust_consistency_over_promises.json",
)
FIG_RE = re.compile(r"figure in side profile", re.I)
NOPERS_RE = re.compile(r"no person, no face", re.I)
PAVE_RE = re.compile(r"dry pavement", re.I)
MAN_RE = re.compile(r"a quiet man", re.I)
NEGATION_RE = re.compile(r"\b(?:not|no|never)\s+(?:a |an |the )?[a-z]", re.I)
KITCHEN_RE = re.compile(r"kitchen counter", re.I)
INTERIOR_THEMES = {
    "self_respect",
    "communication",
    "attachment",
    "grief",
}


def _audit(rec: dict) -> list[str]:
    fails: list[str] = []
    theme = str(rec.get("theme") or "")
    script = rec.get("script") or {}
    lines = [r for r in (script.get("lines") or []) if isinstance(r, dict)]
    for i, row in enumerate(lines, 1):
        p = str(row.get("visual_prompt") or "")
        st = str(row.get("subject_type") or "")
        if st == "object_focus" and FIG_RE.search(p):
            fails.append(f"{theme} beat {i}: object-only still has figure-in-profile")
        if st == "object_focus" and FIG_RE.search(p) and NOPERS_RE.search(p):
            fails.append(f"{theme} beat {i}: figure+no-person contradiction")
        if theme in INTERIOR_THEMES and PAVE_RE.search(p):
            fails.append(f"{theme} beat {i}: dry pavement on interior")
        if MAN_RE.search(p):
            fails.append(f"{theme} beat {i}: quiet man (POV is a woman)")
        if st == "object_focus" and KITCHEN_RE.search(p):
            fails.append(f"{theme} beat {i}: object-focus restates kitchen counter")
        if NEGATION_RE.search(p):
            fails.append(f"{theme} beat {i}: leftover not/no/never in positive prompt")
        if theme == "trust" and i in {5, 6, 7}:
            if st != "couple":
                fails.append(f"{theme} beat {i}: expected couple, got {st}")
            if "a man and a woman" not in p.lower() and "two figures" not in p.lower():
                fails.append(f"{theme} beat {i}: second person missing from prompt")
    return fails


def _refresh_stills(rec: dict) -> None:
    script = rec.get("script") or {}
    lines = [r for r in (script.get("lines") or []) if isinstance(r, dict)]
    rec["spoken_lines"] = [str(r.get("text") or "") for r in lines]
    stills = []
    for i, row in enumerate(lines):
        stills.append(
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
                "subject_type": row.get("subject_type"),
                "visual_prompt": row.get("visual_prompt") or "",
            }
        )
    rec["stills"] = stills
    world = script.get("episode_atmosphere") or {}
    if not world and lines:
        world = {
            "id": lines[0].get("episode_world_id"),
            "place": lines[0].get("atmosphere_place"),
            "mood": lines[0].get("atmosphere_mood"),
            "light_quality": lines[0].get("atmosphere_light"),
        }
    rec["visual_world"] = {
        "id": world.get("id"),
        "line": (
            f"{world.get('place')}. Mood: {world.get('mood')}. "
            f"Light: {world.get('light_quality')}."
        ),
        "place": world.get("place"),
        "mood": world.get("mood"),
        "light_quality": world.get("light_quality"),
    }


def _write_pack_md(rows: list[dict]) -> None:
    parts = [
        "# Five-script review pack (no generation)",
        "",
        "All five passed live story-quality. Hope was not touched.",
        "Spoken lines are frozen. Prompts restamped 2026-08-25 (cast, object-only, pavement).",
        "",
    ]
    for rec in rows:
        story = rec.get("story") or {}
        spine = int(story.get("spine_score") or 0)
        stakes = int(story.get("stakes_score") or 0)
        parts.extend(
            [
                "---",
                f"## {rec.get('emotion')}",
                f"Theme: {rec.get('theme')} / {rec.get('subtheme')}",
                f"Gate: spine {spine}/8 (need 6), stakes={stakes}, close pass",
                "",
                "**Thesis**",
                str(rec.get("thesis") or ""),
                "",
                "**Nine spoken lines**",
            ]
        )
        for i, line in enumerate(rec.get("spoken_lines") or [], 1):
            parts.append(f"{i}. {line}")
        parts.extend(
            [
                "",
                "**Causal spine** (line → connective → job)",
                "| # | line | connective | job | link |",
                "|---|------|------------|-----|------|",
            ]
        )
        for row in rec.get("spine_table") or []:
            parts.append(
                f"| {row.get('beat')} | {row.get('line')} | {row.get('connective')} "
                f"| {row.get('job')} | {row.get('link')} |"
            )
        world = rec.get("visual_world") or {}
        parts.extend(
            [
                "",
                "**Visual world**",
                str(world.get("line") or ""),
                "",
            ]
        )
        for still in rec.get("stills") or []:
            parts.append(f"**Beat {still.get('beat')}** — {still.get('line')}")
            parts.append(
                f"- camera: angle={still.get('angle')}; distance={still.get('distance')}; "
                f"light={still.get('light')}; time={still.get('time_of_day')}"
            )
            parts.append(
                f"- in-world: setting={still.get('setting')}; object={still.get('key_object')}; "
                f"subject={still.get('subject_type')}; background={still.get('background')}"
            )
            parts.append("")
            parts.append(str(still.get("visual_prompt") or ""))
            parts.append("")
        parts.append("")
    dest = PACK / "PACK.md"
    dest.write_text("\n".join(parts).rstrip() + "\n", encoding="utf-8")
    print("wrote", dest)


def _restamp_one(path: Path) -> dict:
    rec = json.loads(path.read_text(encoding="utf-8"))
    script = rec.get("script")
    if not isinstance(script, dict):
        raise SystemExit(f"no script in {path}")
    lines = [r for r in (script.get("lines") or []) if isinstance(r, dict)]
    frozen = [str(r.get("text") or r.get("beat_text") or "") for r in lines]
    world_id = str(
        (rec.get("visual_world") or {}).get("id")
        or lines[0].get("force_episode_world_id")
        or lines[0].get("episode_world_id")
        or ""
    )
    for row in lines:
        row["text"] = str(row.get("text") or row.get("beat_text") or "")
        row["beat_text"] = row["text"]
        row["episode_theme"] = str(script.get("theme") or rec.get("theme") or "")
        row["episode_module"] = str(script.get("module") or "relationship")
        row["episode_thesis"] = str(script.get("thesis") or rec.get("thesis") or "")
        if world_id:
            row["force_episode_world_id"] = world_id
        row.pop("composition_type", None)
    apply_v2_prompts_to_lines_dev(lines, lock_visuals=False)
    after = [str(r.get("text") or "") for r in lines]
    if after != frozen:
        raise SystemExit(f"spoken lines changed in {path.name}: {frozen!r} -> {after!r}")
    if lines and isinstance(lines[0].get("episode_atmosphere"), dict):
        script["episode_atmosphere"] = dict(lines[0]["episode_atmosphere"])
    script["lines"] = lines
    rec["script"] = script
    _refresh_stills(rec)
    path.write_text(json.dumps(rec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("restamped", path.name)
    return rec


def main() -> int:
    rows: list[dict] = []
    fails: list[str] = []
    for name in PACK_FILES:
        rec = _restamp_one(PACK / name)
        rows.append(rec)
        fails.extend(_audit(rec))
    index = []
    for rec, name in zip(rows, PACK_FILES):
        lite = {k: rec[k] for k in rec if k != "script"}
        index.append({"path": str(PACK / name), **lite})
    (PACK / "index.json").write_text(
        json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    _write_pack_md(rows)
    if fails:
        print("AUDIT FAIL")
        for f in fails:
            print(" -", f)
        return 1
    print("AUDIT PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
