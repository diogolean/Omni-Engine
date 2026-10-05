# -*- coding: utf-8 -*-
"""Retrofit spoken-budget gate onto 4 locked scripts. No stills / no assemble."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=True, encoding="utf-8-sig")

from agents.writer.freeform_writer import split_long_line  # noqa: E402
from agents.writer.judge_gate import judge_draft  # noqa: E402
from agents.writer.spoken_budget import (  # noqa: E402
    apply_draft_to_script,
    assess_lines,
    count_words,
    must_keep_from_rows,
    rewrite_single_line,
    script_to_draft,
    tighten_script_dict,
)
from core.economic_reel_lofi import config as lofi_cfg  # noqa: E402
from core.economic_reel_lofi.review_gates import write_approval_artifact  # noqa: E402
from core.economic_reel_lofi.visual_identity import (  # noqa: E402
    assign_distinct_hook_varieties,
    hook_silhouette_dissolve_scene,
    pick_hook_dissolve,
    pick_hook_memory_overlay,
)

CLIPS = ROOT / "outputs/wonder_feed/clips"
OUT_DIR = CLIPS / "freeform_drafts"
REVIEW_DIR = CLIPS / "approval_required"

PACKS: tuple[dict[str, str], ...] = (
    {
        "id": "forgiveness",
        "locked": "locked_freeform_02_forgiveness.json",
        "stills": "lofi_stills_forgiveness_the_apology_that_never_came_20260826_223328_v02.json",
        "out": "tightened_locked_02_forgiveness.json",
    },
    {
        "id": "starting_over",
        "locked": "locked_freeform_03_starting_over.json",
        "stills": "lofi_stills_starting_over_after_it_ended_20260826_224216_v03.json",
        "out": "tightened_locked_03_starting_over.json",
    },
    {
        "id": "suffer_imagination",
        "locked": "locked_freeform_04_We_suffer_more_often_in.json",
        "stills": "lofi_stills_hope_20260826_225023_v04.json",
        "out": "tightened_locked_04_suffer_imagination.json",
    },
    {
        "id": "wound_light",
        "locked": "locked_freeform_05_The_wound_is_the_place_w.json",
        "stills": "lofi_stills_hope_20260826_225946_v05.json",
        "out": "tightened_locked_05_wound_light.json",
    },
)


def _stamp_captions(text: str) -> list[str]:
    max_w, max_c = lofi_cfg.thematic_caption_limits()
    return split_long_line(text, max_w, max_c)


def _load_json(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"not an object: {path}")
    return raw


def _script_of(blob: dict) -> dict:
    if isinstance(blob.get("script"), dict):
        return dict(blob["script"])
    return dict(blob)


def _extract_hook_current(stills_script: dict) -> dict:
    row = next(
        (r for r in (stills_script.get("lines") or []) if isinstance(r, dict) and int(r.get("scene") or 0) == 1),
        {},
    )
    prompt = str(row.get("visual_concept") or row.get("scene_description") or row.get("visual_prompt") or "")
    return {
        "setting": row.get("setting"),
        "key_object": row.get("key_object"),
        "palette_key": row.get("palette_key") or row.get("palette_temp"),
        "visual_concept": prompt,
        "has_close_up_profile": "close-up profile silhouette" in prompt.lower()
        or "close-up profile" in prompt.lower(),
        "has_sepia_teal": "sepia-and-teal" in prompt.lower(),
    }


def _proposed_hook(script: dict, variety: dict, stills_row: dict) -> dict:
    theme = str(script.get("theme") or "")
    thesis = str(script.get("thesis") or "")
    row0 = next(
        (r for r in (script.get("lines") or []) if isinstance(r, dict)),
        {},
    )
    meaning = str(row0.get("text") or "")
    motif = str(stills_row.get("key_object") or row0.get("key_object") or "")
    dissolve = pick_hook_dissolve(theme, thesis, meaning)
    if "bird" not in dissolve.lower():
        dissolve = "a flock of small birds breaking from the silhouette at the head"
    overlay = pick_hook_memory_overlay(theme, thesis, meaning, motif)
    prompt = hook_silhouette_dissolve_scene(
        dissolve_element=dissolve,
        memory_overlay=overlay,
        angle=variety["angle"],
        gaze=variety["gaze"],
        palette_key=variety["palette_key"],
    )
    return {
        "angle": variety["angle"],
        "gaze": variety["gaze"],
        "palette_key": variety["palette_key"],
        "duotone": variety.get("duotone"),
        "dissolve_element": dissolve,
        "hook_memory_overlay": overlay,
        "visual_concept": prompt,
        "setting": stills_row.get("setting"),
        "key_object": stills_row.get("key_object"),
    }


def _tts_verify(draft, keep: dict[int, list[str]], theme: str, subtheme: str) -> tuple[list[dict], str | None]:
    from agents.media.audio_engine import generate_voiceover_with_timestamps
    from core.economic_reel_lofi.assembler import measure_vo_speech_duration
    from core.economic_reel_lofi.pipeline import (
        _sanitize_caption_typos,
        _tts_breath_commas,
        _tts_text_with_breaks,
    )

    work_dir = Path(tempfile.gettempdir()) / "lofi_tighten_tts" / theme
    work_dir.mkdir(parents=True, exist_ok=True)
    beat_s = float(lofi_cfg.beat_duration_s())
    ceiling = lofi_cfg.beat_word_ceiling(beat_s)
    voice_id = lofi_cfg.tts_voice_id()
    model_id = lofi_cfg.tts_model()
    speed = lofi_cfg.tts_speed()
    measured: list[dict] = []
    durations = [beat_s] * len(draft.lines)

    for i, line in enumerate(list(draft.lines)):
        caption = _sanitize_caption_typos(line)
        vo_path = work_dir / f"vo_tight_scene_{i + 1:02d}.mp3"
        tts_text = _tts_text_with_breaks(_tts_breath_commas(caption))
        vo_path, raw_timings = generate_voiceover_with_timestamps(
            tts_text,
            vo_path,
            voice_id=voice_id or None,
            model_id=model_id,
            force_elevenlabs=True,
            expressive_mode=False,
            enable_ssml="<break" in tts_text,
            speed=speed,
            voice_settings={
                "stability": 1.0,
                "similarity_boost": 1.0,
                "style": 0.0,
                "use_speaker_boost": True,
                "speed": speed,
            },
        )
        vo_dur = float(measure_vo_speech_duration(vo_path)) if vo_path and vo_path.is_file() else 0.0
        declared = durations[i]
        if lofi_cfg.vo_duration_overrun(vo_dur, duration_s=declared):
            print(f"[TTS] {theme} beat={i + 1} {vo_dur:.2f}s OVER {declared:.1f}s — rewrite")
            keep_i = keep.get(i) or []
            tts_ceiling = max(len(keep_i) + 2, min(ceiling - 2, count_words(caption) - 2))
            try:
                caption = rewrite_single_line(
                    caption,
                    ceiling=tts_ceiling,
                    theme=theme,
                    subtheme=subtheme,
                    neighbor_before=draft.lines[i - 1] if i > 0 else "",
                    neighbor_after=draft.lines[i + 1] if i + 1 < len(draft.lines) else "",
                    must_keep=keep_i,
                )
                draft.lines[i] = caption
                tts_text = _tts_text_with_breaks(_tts_breath_commas(caption))
                vo_path, raw_timings = generate_voiceover_with_timestamps(
                    tts_text,
                    vo_path,
                    voice_id=voice_id or None,
                    model_id=model_id,
                    force_elevenlabs=True,
                    expressive_mode=False,
                    enable_ssml="<break" in tts_text,
                    speed=speed,
                    voice_settings={
                        "stability": 1.0,
                        "similarity_boost": 1.0,
                        "style": 0.0,
                        "use_speaker_boost": True,
                        "speed": speed,
                    },
                )
                vo_dur = float(measure_vo_speech_duration(vo_path)) if vo_path and vo_path.is_file() else 0.0
            except Exception as exc:  # noqa: BLE001
                print(f"[TTS] {theme} beat={i + 1} rewrite failed: {exc}")
        rec = {
            "scene": i + 1,
            "text": caption,
            "words": count_words(caption),
            "vo_dur_s": round(vo_dur, 3),
            "declared_s": declared,
            "limit_s": round(declared * (1.0 + float(lofi_cfg.TTS_DURATION_TOLERANCE)), 3),
            "overrun": lofi_cfg.vo_duration_overrun(vo_dur, duration_s=declared),
        }
        measured.append(rec)
        print(
            f"  beat {i + 1}: {vo_dur:.2f}s / {declared:.1f}s "
            f"{'OVER' if rec['overrun'] else 'ok'}  {caption!r}"
        )

    overruns = [
        {
            "index": i,
            "vo_dur": rec["vo_dur_s"],
            "duration_s": durations[i],
            "tightened": True,
        }
        for i, rec in enumerate(measured)
        if rec["overrun"]
    ]
    if overruns:
        applied, stop = lofi_cfg.apply_isolated_tts_duration_bumps(
            overruns,
            requested_total_s=beat_s * len(draft.lines),
            n_beats=len(draft.lines),
        )
        if stop:
            return measured, stop
        for note in applied:
            idx = int(note["index"])
            bumped = float(note["bumped_s"])
            durations[idx] = bumped
            measured[idx]["declared_s"] = bumped
            measured[idx]["overrun"] = lofi_cfg.vo_duration_overrun(
                measured[idx]["vo_dur_s"], duration_s=bumped
            )
            print(f"[TTS] auto-bump beat {idx + 1} → {bumped:.1f}s")
        leftover = [r for r in measured if r["overrun"]]
        if leftover:
            return measured, f"TTS overrun remains on {[r['scene'] for r in leftover]}"
    return measured, None


def _process_pack(spec: dict[str, str], variety: dict[str, str]) -> dict:
    locked_path = OUT_DIR / spec["locked"]
    stills_path = CLIPS / spec["stills"]
    print("\n" + "=" * 72)
    print(f"PACK {spec['id']} ← {locked_path.name}")
    locked = _load_json(locked_path)
    stills = _load_json(stills_path)
    script = _script_of(locked)
    stills_script = _script_of(stills)
    stills_rows = [r for r in (stills_script.get("lines") or []) if isinstance(r, dict)]
    rows = [r for r in (script.get("lines") or []) if isinstance(r, dict)]
    keep = must_keep_from_rows(rows, stills_rows=stills_rows)
    before = assess_lines(
        [str(r.get("text") or "") for r in rows],
        duration_s=float(script.get("duration_requested_s") or 27.0),
        beat_s=float(lofi_cfg.beat_duration_s()),
    )
    print(f"  before ok={before.get('ok')} reason={before.get('reason')}")
    for i, k in keep.items():
        print(f"  must_keep beat {i + 1}: {k}")

    script, budget = tighten_script_dict(script, must_keep=keep, stills_rows=stills_rows)
    draft = script_to_draft(script)
    after = budget
    print(f"  after ok={after.get('ok')} reason={after.get('reason')}")

    if not after.get("ok"):
        payload = {
            "ok": False,
            "pack": spec["id"],
            "stop_reason": after.get("reason"),
            "before": before,
            "after": after,
            "must_keep": {str(i + 1): v for i, v in keep.items()},
        }
        out = OUT_DIR / spec["out"]
        out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return payload

    print("  judging…")
    verdict = judge_draft(draft)
    print(f"  judge {verdict.summary() if hasattr(verdict, 'summary') else verdict}")
    script["judge"] = verdict.to_dict() if hasattr(verdict, "to_dict") else {"ok": bool(verdict.ok)}
    if not verdict.ok:
        payload = {
            "ok": False,
            "pack": spec["id"],
            "stop_reason": "judge failed",
            "before": before,
            "after": after,
            "judge": script["judge"],
            "script": script,
        }
        out = OUT_DIR / spec["out"]
        out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return payload

    print("  TTS verify…")
    measured, tts_stop = _tts_verify(
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
    script["monologue"] = " ".join(draft.lines)

    current_hook = _extract_hook_current(stills_script)
    stills_row0 = stills_rows[0] if stills_rows else {}
    proposed_hook = _proposed_hook(script, variety, stills_row0)

    # Stamp proposed hook + stills visual fields onto the review script only.
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
        ln["caption_beats"] = _stamp_captions(str(ln.get("text") or ""))
        lines_out.append(ln)
    script["lines"] = lines_out
    script["spoken_budget"] = after
    script["spoken_budget_ok"] = bool(after.get("ok")) and not tts_stop

    beats_table = []
    for i, rec in enumerate(measured):
        beats_table.append(
            {
                "scene": rec["scene"],
                "original": (rows[i].get("text") if i < len(rows) else ""),
                "original_words": count_words(rows[i].get("text") if i < len(rows) else ""),
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
    print(f"  wrote {out}")
    return payload


def main() -> int:
    keys = [p["id"] for p in PACKS]
    varieties = assign_distinct_hook_varieties(keys)
    results = []
    for spec in PACKS:
        results.append(_process_pack(spec, varieties[spec["id"]]))

    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
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

    md = [
        "# Approval required — 4 tightened LOFI scripts",
        "",
        "Word-budget gate is now the default on script generation (compose, generate_script, and `--lofi-script`).",
        "Distance is untouched. No new stills or renders for these four packs.",
        "",
        "## Scene-1 hook comparison (template repetition check)",
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
        md.append(f"### {r.get('pack')} — ok={r.get('ok')} judge={((r.get('judge') or {}).get('pass') if isinstance(r.get('judge'), dict) else r.get('ok'))}")
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
        md.append(f"**Proposed scene-1 hook:** angle=`{prop.get('angle')}` gaze=`{prop.get('gaze')}` palette=`{prop.get('palette_key')}`")
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
    # Also write a stable latest pointer
    latest = REVIEW_DIR / "lofi_approval_four_packs_latest.md"
    latest.write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"\nAPPROVAL ARTIFACT {json_path}")
    print(f"APPROVAL ARTIFACT {md_path}")
    failed = [r.get("pack") for r in results if not r.get("ok")]
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
