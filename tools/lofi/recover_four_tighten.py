# -*- coding: utf-8 -*-
"""Recover judge/TTS fails on the 4-pack tighten. No stills / no assemble."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "lofi"))

os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=True, encoding="utf-8-sig")

import tighten_four_scripts as t4  # noqa: E402
from agents.mcp.text_model import complete_script  # noqa: E402
from agents.writer.judge_gate import judge_draft  # noqa: E402
from agents.writer.spoken_budget import (  # noqa: E402
    _extract_json,
    _REWRITE_SYSTEM,
    _writer_provider,
    apply_draft_to_script,
    assess_lines,
    count_words,
    missing_keep_terms,
    rewrite_single_line,
    script_to_draft,
)
from core.economic_reel_lofi import config as lofi_cfg  # noqa: E402
from core.economic_reel_lofi.visual_identity import assign_distinct_hook_varieties  # noqa: E402

OUT_DIR = t4.OUT_DIR
REVIEW_DIR = t4.REVIEW_DIR


def _rewrite_piece_for_judge(draft, *, feedback: str, keep: dict[int, list[str]], ceiling: int) -> None:
    numbered = "\n".join(f"{i + 1}. {line}" for i, line in enumerate(draft.lines))
    keep_bits = "\n".join(
        f"Beat {i + 1} MUST keep: {', '.join(v)}" for i, v in keep.items() if v
    )
    prompt = (
        f"SUBJECT: {(draft.brief.theme if draft.brief else '')}\n"
        f"HARD MAX {ceiling} words per beat. Same situation and metaphor. "
        "Full spoken clauses, not telegraphic labels. Restore feeling inside the physical acts.\n"
        f"JUDGE NOTE: {feedback}\n"
        f"{keep_bits}\n\n"
        f"Current beats:\n{numbered}\n"
        'Return JSON: {"lines": ["...", "..."]}'
    )
    result = complete_script(
        prompt, system=_REWRITE_SYSTEM, provider=_writer_provider(), kind="writer"
    )
    data = _extract_json(result.text) or {}
    lines = [str(x).strip() for x in (data.get("lines") or []) if str(x).strip()]
    if len(lines) != len(draft.lines):
        raise ValueError(f"rewrite returned {len(lines)} lines, expected {len(draft.lines)}")
    for i, text in enumerate(lines):
        if count_words(text) > ceiling:
            text = rewrite_single_line(
                text,
                ceiling=ceiling,
                theme=(draft.brief.theme if draft.brief else "") or "",
                subtheme=(draft.brief.subtheme if draft.brief else "") or "",
                neighbor_before=lines[i - 1] if i > 0 else "",
                neighbor_after=lines[i + 1] if i + 1 < len(lines) else "",
                must_keep=keep.get(i) or [],
            )
        missing = missing_keep_terms(text, keep.get(i) or [])
        if missing:
            raise ValueError(f"beat {i + 1} dropped {missing}: {text!r}")
        if count_words(text) > ceiling:
            raise ValueError(f"beat {i + 1} still over ceiling: {text!r}")
        lines[i] = text
    draft.lines = lines


def _finish_pack(spec: dict, script: dict, draft, keep, variety, stills_script, stills_rows, before, after, verdict) -> dict:
    apply_draft_to_script(script, draft)
    script["judge"] = verdict.to_dict()
    print(f"  TTS verify {spec['id']}…")
    measured, tts_stop = t4._tts_verify(
        draft,
        keep,
        str(script.get("theme") or spec["id"]),
        str(script.get("subtheme") or ""),
    )
    apply_draft_to_script(script, draft)
    for i, rec in enumerate(measured):
        if i < len(script["lines"]):
            script["lines"][i]["duration_s"] = rec["declared_s"]
            script["lines"][i]["tts_vo_dur_s"] = rec["vo_dur_s"]
    current_hook = t4._extract_hook_current(stills_script)
    stills_row0 = stills_rows[0] if stills_rows else {}
    proposed_hook = t4._proposed_hook(script, variety, stills_row0)
    lines_out = []
    for i, row in enumerate(script.get("lines") or []):
        ln = dict(row)
        still = stills_rows[i] if i < len(stills_rows) else {}
        ln["setting"] = still.get("setting") or ln.get("setting")
        ln["key_object"] = still.get("key_object") or ln.get("key_object")
        ln["palette_key"] = still.get("palette_key") or ln.get("palette_key")
        ln["visual_concept"] = still.get("visual_concept") or still.get("scene_description")
        if i == 0:
            ln["hook_angle"] = proposed_hook["angle"]
            ln["hook_gaze"] = proposed_hook["gaze"]
            ln["palette_key"] = proposed_hook["palette_key"]
            ln["visual_concept"] = proposed_hook["visual_concept"]
            ln["scene_description"] = proposed_hook["visual_concept"]
            ln["dissolve_element"] = proposed_hook["dissolve_element"]
            ln["hook_memory_overlay"] = proposed_hook["hook_memory_overlay"]
        ln["caption_beats"] = t4._stamp_captions(str(ln.get("text") or ""))
        lines_out.append(ln)
    script["lines"] = lines_out
    script["spoken_budget"] = after
    script["spoken_budget_ok"] = bool(after.get("ok")) and not tts_stop
    original_rows = [r for r in (t4._script_of(t4._load_json(OUT_DIR / spec["locked"])).get("lines") or []) if isinstance(r, dict)]
    beats_table = []
    for i, rec in enumerate(measured):
        orig = original_rows[i].get("text") if i < len(original_rows) else ""
        beats_table.append(
            {
                "scene": rec["scene"],
                "original": orig,
                "original_words": count_words(orig),
                "tightened": rec["text"],
                "tightened_words": rec["words"],
                "duration_s": rec["declared_s"],
                "vo_dur_s": rec["vo_dur_s"],
                "must_keep": keep.get(i) or [],
                "setting": lines_out[i].get("setting") if i < len(lines_out) else None,
                "key_object": lines_out[i].get("key_object") if i < len(lines_out) else None,
                "palette_key": lines_out[i].get("palette_key") if i < len(lines_out) else None,
                "visual_concept": lines_out[i].get("visual_concept") if i < len(lines_out) else None,
            }
        )
    payload = {
        "ok": bool(after.get("ok")) and verdict.ok and not tts_stop,
        "approval_required": True,
        "pack": spec["id"],
        "stop_reason": tts_stop,
        "before": before,
        "after": after,
        "judge": script["judge"],
        "tts": measured,
        "beats": beats_table,
        "current_scene1_hook": current_hook,
        "proposed_scene1_hook": proposed_hook,
        "script": script,
        "distance_untouched": True,
        "no_new_stills": True,
        "no_new_renders": True,
    }
    out = OUT_DIR / spec["out"]
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"  wrote {out} ok={payload['ok']}")
    return payload


def main() -> int:
    specs = {p["id"]: p for p in t4.PACKS}
    varieties = assign_distinct_hook_varieties(list(specs))
    results = []

    # forgiveness already passed — reload
    results.append(t4._load_json(OUT_DIR / specs["forgiveness"]["out"]))

    # starting_over: judge recovery
    spec = specs["starting_over"]
    blob = t4._load_json(OUT_DIR / spec["out"])
    script = dict(blob.get("script") or {})
    stills = t4._load_json(t4.CLIPS / spec["stills"])
    stills_script = t4._script_of(stills)
    stills_rows = [r for r in (stills_script.get("lines") or []) if isinstance(r, dict)]
    draft = script_to_draft(script)
    keep = {int(k) - 1 if str(k).isdigit() else int(k): v for k, v in (blob.get("must_keep") or {}).items()} if blob.get("must_keep") else t4.must_keep_from_rows(
        [r for r in (script.get("lines") or []) if isinstance(r, dict)],
        stills_rows=stills_rows,
    ) if hasattr(t4, "must_keep_from_rows") else None
    from agents.writer.spoken_budget import must_keep_from_rows
    rows = [r for r in (script.get("lines") or []) if isinstance(r, dict)]
    keep = must_keep_from_rows(rows, stills_rows=stills_rows)
    feedback = str(((blob.get("judge") or {}).get("revision_note")) or "")
    print("\n=== recover starting_over judge ===")
    _rewrite_piece_for_judge(draft, feedback=feedback, keep=keep, ceiling=lofi_cfg.beat_word_ceiling())
    after = assess_lines(draft.lines, duration_s=27.0, beat_s=3.0)
    print("  after", after.get("ok"), after.get("reason"))
    apply_draft_to_script(script, draft)
    verdict = judge_draft(draft)
    print("  judge", verdict.summary())
    if not after.get("ok") or not verdict.ok:
        blob["ok"] = False
        blob["stop_reason"] = "judge recovery failed"
        blob["after"] = after
        blob["judge"] = verdict.to_dict()
        blob["script"] = script
        (OUT_DIR / spec["out"]).write_text(json.dumps(blob, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        results.append(blob)
    else:
        results.append(
            _finish_pack(spec, script, draft, keep, varieties[spec["id"]], stills_script, stills_rows, blob["before"], after, verdict)
        )

    # suffer_imagination: shorten TTS overruns
    spec = specs["suffer_imagination"]
    blob = t4._load_json(OUT_DIR / spec["out"])
    script = dict(blob.get("script") or {})
    stills = t4._load_json(t4.CLIPS / spec["stills"])
    stills_script = t4._script_of(stills)
    stills_rows = [r for r in (stills_script.get("lines") or []) if isinstance(r, dict)]
    draft = script_to_draft(script)
    keep = must_keep_from_rows(
        [r for r in (script.get("lines") or []) if isinstance(r, dict)],
        stills_rows=stills_rows,
    )
    print("\n=== recover suffer_imagination TTS ===")
    for i in (0, 1, 3):
        keep_i = keep.get(i) or []
        draft.lines[i] = rewrite_single_line(
            draft.lines[i],
            ceiling=6,
            theme=str(script.get("theme") or ""),
            subtheme=str(script.get("subtheme") or ""),
            neighbor_before=draft.lines[i - 1] if i > 0 else "",
            neighbor_after=draft.lines[i + 1] if i + 1 < len(draft.lines) else "",
            must_keep=keep_i,
        )
        print(f"  beat {i + 1} → {draft.lines[i]!r}")
    after = assess_lines(
        draft.lines,
        duration_s=float(lofi_cfg.declared_duration_s(scene_count=len(draft.lines))),
        beat_s=3.0,
    )
    apply_draft_to_script(script, draft)
    verdict = judge_draft(draft)
    print("  judge", verdict.summary())
    results.append(
        _finish_pack(spec, script, draft, keep, varieties[spec["id"]], stills_script, stills_rows, blob["before"], after, verdict)
    )

    # wound_light: rewrite beat 3 only
    spec = specs["wound_light"]
    blob = t4._load_json(OUT_DIR / spec["out"])
    script = dict(blob.get("script") or {})
    stills = t4._load_json(t4.CLIPS / spec["stills"])
    stills_script = t4._script_of(stills)
    stills_rows = [r for r in (stills_script.get("lines") or []) if isinstance(r, dict)]
    draft = script_to_draft(script)
    keep = must_keep_from_rows(
        [r for r in (script.get("lines") or []) if isinstance(r, dict)],
        stills_rows=stills_rows,
    )
    print("\n=== recover wound_light beat 3 ===")
    draft.lines[2] = rewrite_single_line(
        "People love to say the wound is where the light gets in, like it happens on its own.",
        ceiling=9,
        theme=str(script.get("theme") or "wound"),
        subtheme=str(script.get("subtheme") or ""),
        neighbor_before=draft.lines[1],
        neighbor_after=draft.lines[3],
        must_keep=keep.get(2) or [],
    )
    print(f"  beat 3 → {draft.lines[2]!r}")
    after = assess_lines(
        draft.lines,
        duration_s=float(lofi_cfg.declared_duration_s(scene_count=len(draft.lines))),
        beat_s=3.0,
    )
    apply_draft_to_script(script, draft)
    verdict = judge_draft(draft)
    print("  judge", verdict.summary())
    if not verdict.ok:
        blob["ok"] = False
        blob["stop_reason"] = "judge recovery failed"
        blob["after"] = after
        blob["judge"] = verdict.to_dict()
        blob["script"] = script
        (OUT_DIR / spec["out"]).write_text(json.dumps(blob, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        results.append(blob)
    else:
        results.append(
            _finish_pack(spec, script, draft, keep, varieties[spec["id"]], stills_script, stills_rows, blob["before"], after, verdict)
        )

    # rebuild combined artifact (reuse t4.main's markdown path)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    combined = {
        "approval_required": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "word_budget_gate_is_default": True,
        "distance_untouched": True,
        "no_new_stills": True,
        "no_new_renders": True,
        "hook_varieties": varieties,
        "scene1_hook_comparison": [
            {
                "pack": r.get("pack"),
                "current": r.get("current_scene1_hook"),
                "proposed": r.get("proposed_scene1_hook"),
            }
            for r in results
        ],
        "episodes": results,
        "stop": (
            "Do not generate images or assemble for forgiveness / starting_over / "
            "suffer_imagination / wound_light until this artifact is explicitly approved."
        ),
    }
    json_path = REVIEW_DIR / f"lofi_approval_four_packs_{stamp}.json"
    json_path.write_text(json.dumps(combined, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    # call t4 markdown builder by faking PACKS loop
    md = [
        "# Approval required — 4 tightened LOFI scripts",
        "",
        "Word-budget gate is now the default on script generation.",
        "Distance is untouched. No new stills or renders for these four packs.",
        "",
        "## Scene-1 hook comparison",
        "",
        "| pack | current close-up profile? | current sepia-teal? | proposed angle | proposed gaze | proposed palette |",
        "|------|---------------------------|---------------------|----------------|---------------|------------------|",
    ]
    for r in results:
        cur = r.get("current_scene1_hook") or {}
        prop = r.get("proposed_scene1_hook") or {}
        md.append(
            f"| {r.get('pack')} | {cur.get('has_close_up_profile')} | "
            f"{cur.get('has_sepia_teal')} | {prop.get('angle')} | "
            f"{prop.get('gaze')} | {prop.get('palette_key')} |"
        )
    md.extend(["", "## Per-episode beats", ""])
    for r in results:
        j = r.get("judge") or {}
        md.append(f"### {r.get('pack')} — ok={r.get('ok')} judge={j.get('pass') if isinstance(j, dict) else r.get('ok')}")
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
        md.append(
            f"**Proposed scene-1 hook:** angle=`{prop.get('angle')}` gaze=`{prop.get('gaze')}` palette=`{prop.get('palette_key')}`"
        )
        md.append("")
        md.append(str(prop.get("visual_concept") or ""))
        md.append("")
    md.extend(
        [
            "---",
            "",
            "STOP. Reply with explicit approval of this artifact before any image generation.",
            f"JSON: `{json_path}`",
        ]
    )
    md_path = json_path.with_suffix(".md")
    md_path.write_text("\n".join(md) + "\n", encoding="utf-8")
    (REVIEW_DIR / "lofi_approval_four_packs_latest.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"APPROVAL ARTIFACT {json_path}")
    failed = [r.get("pack") for r in results if not r.get("ok")]
    print("failed", failed)
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
