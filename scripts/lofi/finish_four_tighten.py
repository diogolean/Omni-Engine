# -*- coding: utf-8 -*-
"""Finish starting_over beat 5 TTS and wound_light beat 3 judge. No images."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "lofi"))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=True, encoding="utf-8-sig")

import tighten_four_scripts as t4  # noqa: E402
from agents.writer.judge_gate import judge_draft  # noqa: E402
from agents.writer.spoken_budget import (  # noqa: E402
    apply_draft_to_script,
    assess_lines,
    must_keep_from_rows,
    rewrite_single_line,
    script_to_draft,
)
from core.economic_reel_lofi.visual_identity import assign_distinct_hook_varieties  # noqa: E402

OUT = t4.OUT_DIR
REVIEW = t4.REVIEW_DIR


def _reload_latest_forgiveness_suffer():
    return (
        t4._load_json(OUT / "tightened_locked_02_forgiveness.json"),
        t4._load_json(OUT / "tightened_locked_04_suffer_imagination.json"),
    )


def main() -> int:
    specs = {p["id"]: p for p in t4.PACKS}
    varieties = assign_distinct_hook_varieties(list(specs))
    from recover_four_tighten import _finish_pack  # type: ignore

    # starting_over: shorten leftover TTS beat 5
    spec = specs["starting_over"]
    blob = t4._load_json(OUT / spec["out"])
    script = dict(blob["script"])
    stills = t4._load_json(t4.CLIPS / spec["stills"])
    stills_script = t4._script_of(stills)
    stills_rows = [r for r in (stills_script.get("lines") or []) if isinstance(r, dict)]
    draft = script_to_draft(script)
    keep = must_keep_from_rows(
        [r for r in (script.get("lines") or []) if isinstance(r, dict)],
        stills_rows=stills_rows,
    )
    print("starting_over beat 5 was", draft.lines[4])
    draft.lines[4] = rewrite_single_line(
        draft.lines[4],
        ceiling=6,
        theme="starting_over",
        subtheme=str(script.get("subtheme") or ""),
        neighbor_before=draft.lines[3],
        neighbor_after=draft.lines[5],
        must_keep=keep.get(4) or [],
    )
    print(" →", draft.lines[4])
    apply_draft_to_script(script, draft)
    after = assess_lines(draft.lines, duration_s=27.0, beat_s=3.0)
    verdict = judge_draft(draft)
    print("judge", verdict.summary())
    so = _finish_pack(
        spec, script, draft, keep, varieties[spec["id"]], stills_script, stills_rows, blob["before"], after, verdict
    )

    # wound_light: concrete sleeve beat 3
    spec = specs["wound_light"]
    blob = t4._load_json(OUT / spec["out"])
    script = dict(blob["script"])
    stills = t4._load_json(t4.CLIPS / spec["stills"])
    stills_script = t4._script_of(stills)
    stills_rows = [r for r in (stills_script.get("lines") or []) if isinstance(r, dict)]
    draft = script_to_draft(script)
    keep = must_keep_from_rows(
        [r for r in (script.get("lines") or []) if isinstance(r, dict)],
        stills_rows=stills_rows,
    )
    draft.lines[2] = rewrite_single_line(
        "They talk like keeping the sleeve down is healing.",
        ceiling=9,
        theme="wound",
        subtheme=str(script.get("subtheme") or ""),
        neighbor_before=draft.lines[1],
        neighbor_after=draft.lines[3],
        must_keep=["sleeve"],
    )
    print("wound beat 3 →", draft.lines[2])
    apply_draft_to_script(script, draft)
    after = assess_lines(
        draft.lines,
        duration_s=30.0,
        beat_s=3.0,
    )
    verdict = judge_draft(draft)
    print("judge", verdict.summary())
    wl = _finish_pack(
        spec, script, draft, keep, varieties[spec["id"]], stills_script, stills_rows, blob["before"], after, verdict
    )

    fg, su = _reload_latest_forgiveness_suffer()
    results = [fg, so, su, wl]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    json_path = REVIEW / f"lofi_approval_four_packs_{stamp}.json"
    combined = {
        "approval_required": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "word_budget_gate_is_default": True,
        "distance_untouched": True,
        "no_new_stills": True,
        "no_new_renders": True,
        "hook_varieties": varieties,
        "scene1_hook_comparison": [
            {"pack": r.get("pack"), "current": r.get("current_scene1_hook"), "proposed": r.get("proposed_scene1_hook")}
            for r in results
        ],
        "episodes": results,
        "stop": "Do not generate images until this artifact is explicitly approved.",
    }
    json_path.write_text(json.dumps(combined, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md = ["# Approval required — 4 tightened LOFI scripts", ""]
    md.append("Word-budget gate is now default. Distance untouched. No new stills/renders.")
    md.extend(["", "## Scene-1 hook comparison", "",
               "| pack | current close-up profile? | current sepia-teal? | proposed angle | proposed gaze | proposed palette |",
               "|------|---------------------------|---------------------|----------------|---------------|------------------|"])
    for r in results:
        cur = r.get("current_scene1_hook") or {}
        prop = r.get("proposed_scene1_hook") or {}
        md.append(
            f"| {r.get('pack')} | {cur.get('has_close_up_profile')} | {cur.get('has_sepia_teal')} | "
            f"{prop.get('angle')} | {prop.get('gaze')} | {prop.get('palette_key')} |"
        )
    md.extend(["", "## Per-episode beats", ""])
    for r in results:
        j = r.get("judge") or {}
        md.append(f"### {r.get('pack')} — ok={r.get('ok')} judge={j.get('pass') if isinstance(j, dict) else None} stop={r.get('stop_reason')}")
        md.append("")
        md.append("| # | orig w | tight w | vo s | dur s | key_object | text |")
        md.append("|---|--------|---------|------|-------|------------|------|")
        for b in r.get("beats") or []:
            md.append(
                f"| {b.get('scene')} | {b.get('original_words')} | {b.get('tightened_words')} | "
                f"{b.get('vo_dur_s')} | {b.get('duration_s')} | {b.get('key_object') or ''} | "
                f"{str(b.get('tightened') or '').replace('|', '/')} |"
            )
        md.append("")
        prop = r.get("proposed_scene1_hook") or {}
        md.append(f"**Proposed scene-1:** `{prop.get('angle')}` / `{prop.get('gaze')}` / `{prop.get('palette_key')}`")
        md.append("")
        md.append(str(prop.get("visual_concept") or ""))
        md.append("")
    md.extend(["---", "", "STOP. Explicit approval required before any image generation.", f"JSON: `{json_path}`"])
    text = "\n".join(md) + "\n"
    json_path.with_suffix(".md").write_text(text, encoding="utf-8")
    (REVIEW / "lofi_approval_four_packs_latest.md").write_text(text, encoding="utf-8")
    print("APPROVAL ARTIFACT", json_path)
    failed = [r.get("pack") for r in results if not r.get("ok")]
    print("failed", failed)
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
