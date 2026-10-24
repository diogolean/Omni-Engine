# -*- coding: utf-8 -*-
"""Tighten the ORIGINAL locked distance script in place. No new story. No still regen."""
from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=True, encoding="utf-8-sig")

from agents.writer.freeform_writer import ScriptDraft, split_long_line  # noqa: E402
from agents.writer.judge_gate import judge_draft  # noqa: E402
from agents.writer.spoken_budget import (  # noqa: E402
    assess_lines,
    count_words,
    enforce_spoken_budget,
    missing_keep_terms,
    rewrite_single_line,
)
from agents.writer.writer_brief import WriterBrief  # noqa: E402
from core.economic_reel_lofi import config as lofi_cfg  # noqa: E402

LOCKED = ROOT / "outputs/wonder_feed/clips/freeform_drafts/locked_freeform_01_distance.json"
STILLS = ROOT / "outputs/wonder_feed/clips/lofi_stills_distance_silence_that_speaks_20260826_222416_v01.json"
OUT_DIR = ROOT / "outputs/wonder_feed/clips/freeform_drafts"

# Spoken nouns the existing stills were built around. Abstract licensed
# fillers (silence, figure, light particles) are not required as spoken words.
MUST_KEEP: dict[int, list[str]] = {
    0: ["line"],
    1: ["quiet"],
    2: ["kettle"],
    3: ["weather", "dog"],
    4: ["weather"],
    5: ["call"],
    6: ["phone"],
    7: ["line"],
    8: [],
}


def _stamp_captions(text: str) -> list[str]:
    max_w, max_c = lofi_cfg.thematic_caption_limits()
    return split_long_line(text, max_w, max_c)


def _continuity_ok(text: str, idx: int, key_object: str, licensed: list[str]) -> dict:
    keep = list(MUST_KEEP.get(idx) or [])
    missing = missing_keep_terms(text, keep)
    blob = text.lower()
    key = str(key_object or "").strip().lower()
    licensed_l = [str(x).strip().lower() for x in licensed if str(x).strip()]
    spoken_matches_key = False
    if key and key not in {"silence", "figure", "light particles"}:
        spoken_matches_key = not missing_keep_terms(text, [key])
    elif key in {"figure", "light particles", "silence"}:
        spoken_matches_key = True  # still is atmosphere/figure, not a spoken prop
    licensed_hits = [n for n in licensed_l if n not in {"silence", "figure", "light particles"} and n in blob]
    return {
        "must_keep": keep,
        "missing_keep": missing,
        "key_object": key_object,
        "licensed_objects": licensed,
        "spoken_matches_key_object": spoken_matches_key,
        "licensed_nouns_in_line": licensed_hits,
        "ok": not missing,
    }


def main() -> int:
    locked = json.loads(LOCKED.read_text(encoding="utf-8"))
    stills = json.loads(STILLS.read_text(encoding="utf-8"))
    script = dict(locked.get("script") or {})
    original_lines = [str(r.get("text") or "") for r in script["lines"]]
    still_lines = list((stills.get("script") or {}).get("lines") or [])

    before = assess_lines(original_lines, duration_s=27.0, beat_s=3.0)
    print("=== ORIGINAL word budget ===")
    for b in before["beats"]:
        mark = "ok" if b["ok"] else "OVER"
        print(f"  beat {b['scene']}: {b['words']}w / {b['ceiling']}w {mark}  {b['text']!r}")

    brief = WriterBrief.from_theme(
        theme="distance",
        subtheme="silence_that_speaks",
        module="relationship",
        meta={"duration_s": 27.0, "beat_duration_s": 3.0},
    )
    draft = ScriptDraft(
        lines=list(original_lines),
        human_situation=str(script.get("human_situation") or ""),
        structure=str(script.get("writer_structure") or ""),
        closing_tool=str(script.get("closing_tool") or ""),
        brief=brief,
        attempt=0,
        provider="locked",
    )
    draft, after = enforce_spoken_budget(draft, must_keep=MUST_KEEP)

    print("\n=== AFTER targeted rewrite ===")
    print(f"ok={after.get('ok')} reason={after.get('reason') or 'none'}")
    rows = []
    continuity_fail = []
    for i, orig in enumerate(original_lines):
        new = draft.lines[i]
        still_row = still_lines[i] if i < len(still_lines) else {}
        key_object = str(still_row.get("key_object") or "")
        licensed = [str(x) for x in (still_row.get("licensed_objects") or [])]
        cont = _continuity_ok(new, i, key_object, licensed)
        rec = {
            "scene": i + 1,
            "original": orig,
            "original_words": count_words(orig),
            "tightened": new,
            "tightened_words": count_words(new),
            "budget": after["budget"],
            "ceiling": after["ceiling"],
            "rewritten": orig != new,
            "word_ok": count_words(new) <= after["ceiling"],
            "continuity": cont,
        }
        rows.append(rec)
        mark = "ok" if rec["word_ok"] else "FAIL"
        print(
            f"  beat {i + 1}: {rec['original_words']}w → {rec['tightened_words']}w / "
            f"{rec['ceiling']}w {mark} keep={cont['must_keep']} missing={cont['missing_keep']}"
        )
        print(f"         was: {orig}")
        print(f"         now: {new}")
        if not rec["word_ok"] or not cont["ok"]:
            continuity_fail.append(rec)

    if continuity_fail or not after.get("ok"):
        print("\nSTOP — some beats could not be tightened without breaking the budget or nouns.")
        payload = {
            "ok": False,
            "stop_reason": "beats could not be tightened inside ceiling while keeping nouns",
            "before": before,
            "after": after,
            "beats": rows,
        }
        out = OUT_DIR / "tightened_locked_01_distance.json"
        out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {out}")
        return 2

    print("\n=== Judge (tightened original, not a new story) ===")
    verdict = judge_draft(draft)
    print(verdict.summary() if hasattr(verdict, "summary") else verdict)
    if not verdict.ok:
        print("STOP — tightened script failed the judge. No TTS / assemble.")
        payload = {
            "ok": False,
            "stop_reason": "judge failed",
            "before": before,
            "after": after,
            "beats": rows,
            "judge": verdict.to_dict(),
        }
        out = OUT_DIR / "tightened_locked_01_distance.json"
        out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {out}")
        return 3

    print("\n=== TTS (real audio, not word estimate) ===")
    from agents.media.audio_engine import generate_voiceover_with_timestamps
    from core.economic_reel_lofi.assembler import measure_vo_speech_duration
    from core.economic_reel_lofi.pipeline import (
        _sanitize_caption_typos,
        _tts_breath_commas,
        _tts_text_with_breaks,
    )

    work_dir = Path(str(stills.get("work_dir") or ""))
    work_dir.mkdir(parents=True, exist_ok=True)
    beat_s = float(lofi_cfg.beat_duration_s())
    ceiling = lofi_cfg.beat_word_ceiling(beat_s)
    voice_paths: list[str] = []
    timings_all: list = []
    measured: list[dict] = []
    tts_fail: list[int] = []

    for i, line in enumerate(draft.lines):
        caption = _sanitize_caption_typos(line)
        vo_path = work_dir / f"vo_tight_scene_{i + 1:02d}.mp3"
        tts_text = _tts_text_with_breaks(_tts_breath_commas(caption))
        use_ssml = "<break" in tts_text
        speed = lofi_cfg.tts_speed()
        model_id = lofi_cfg.tts_model() or "eleven_multilingual_v2"
        voice_id = lofi_cfg.tts_voice_id()
        print(f"[TTS] scene={i + 1} {count_words(caption)}w {caption!r}")
        vo_path, raw_timings = generate_voiceover_with_timestamps(
            tts_text,
            vo_path,
            voice_id=voice_id or None,
            model_id=model_id,
            force_elevenlabs=True,
            expressive_mode=False,
            enable_ssml=use_ssml,
            speed=speed,
            voice_settings={
                "stability": 1.0,
                "similarity_boost": 1.0,
                "style": 0.0,
                "use_speaker_boost": True,
                "speed": speed,
            },
        )
        timings = [
            (str(w), float(s), float(e))
            for w, s, e in (raw_timings or [])
            if str(w).strip() and not str(w).startswith("<") and str(w).lower() not in {"break", "time"}
        ]
        vo_dur = float(measure_vo_speech_duration(vo_path)) if vo_path and vo_path.is_file() else 0.0
        if lofi_cfg.vo_duration_overrun(vo_dur, duration_s=beat_s):
            print(f"[TTS] scene={i + 1} {vo_dur:.2f}s OVER {beat_s:.1f}s — rewrite + re-TTS")
            keep = MUST_KEEP.get(i) or []
            tts_ceiling = max(len(keep) + 2, min(ceiling - 2, count_words(caption) - 2))
            try:
                caption = rewrite_single_line(
                    caption,
                    ceiling=tts_ceiling,
                    theme="distance",
                    subtheme="silence_that_speaks",
                    neighbor_before=draft.lines[i - 1] if i > 0 else "",
                    neighbor_after=draft.lines[i + 1] if i + 1 < len(draft.lines) else "",
                    must_keep=keep,
                )
                missing = missing_keep_terms(caption, keep)
                if missing or count_words(caption) > tts_ceiling:
                    raise ValueError(
                        f"post-TTS rewrite invalid missing={missing} "
                        f"words={count_words(caption)} ceiling={tts_ceiling}"
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
                timings = [
                    (str(w), float(s), float(e))
                    for w, s, e in (raw_timings or [])
                    if str(w).strip() and not str(w).startswith("<") and str(w).lower() not in {"break", "time"}
                ]
                vo_dur = float(measure_vo_speech_duration(vo_path)) if vo_path and vo_path.is_file() else 0.0
            except Exception as exc:  # noqa: BLE001
                print(f"[TTS] scene={i + 1} rewrite failed: {exc}")
        overrun = lofi_cfg.vo_duration_overrun(vo_dur, duration_s=beat_s)
        rec = {
            "scene": i + 1,
            "text": caption,
            "words": count_words(caption),
            "vo_dur_s": round(vo_dur, 3),
            "declared_s": beat_s,
            "limit_s": round(beat_s * (1.0 + float(lofi_cfg.TTS_DURATION_TOLERANCE)), 3),
            "overrun": overrun,
        }
        measured.append(rec)
        print(
            f"  beat {i + 1}: {vo_dur:.2f}s / {beat_s:.1f}s "
            f"(limit {rec['limit_s']:.2f}s) {'OVER' if overrun else 'ok'}  {caption!r}"
        )
        if overrun:
            tts_fail.append(i + 1)
        rows[i]["tightened"] = caption
        rows[i]["tightened_words"] = count_words(caption)
        rows[i]["word_ok"] = count_words(caption) <= ceiling
        rows[i]["tts"] = rec
        voice_paths.append(str(vo_path))
        timings_all.append(timings)

    if tts_fail:
        print(f"\nSTOP — TTS overrun on beats {tts_fail}. No assemble.")
        payload = {
            "ok": False,
            "stop_reason": f"TTS overrun beats={tts_fail}",
            "before": before,
            "after": assess_lines(draft.lines, duration_s=27.0, beat_s=3.0),
            "beats": rows,
            "judge": verdict.to_dict(),
            "tts": measured,
        }
        out = OUT_DIR / "tightened_locked_01_distance.json"
        out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {out}")
        return 4

    print("\n=== Assemble pilot from EXISTING stills + tightened VO ===")
    from core.economic_reel_lofi.regen import assemble_video_from_episode, save_episode_json

    episode = dict(stills)
    new_script = dict(episode.get("script") or {})
    new_lines = []
    for i, row in enumerate(list(new_script.get("lines") or [])):
        ln = dict(row)
        ln["text"] = draft.lines[i]
        ln["beat_text"] = draft.lines[i]
        ln["caption_beats"] = _stamp_captions(draft.lines[i])
        ln["spoken_words"] = count_words(draft.lines[i])
        ln["spoken_word_ceiling"] = ceiling
        ln["duration_s"] = beat_s
        new_lines.append(ln)
    new_script["lines"] = new_lines
    new_script["monologue"] = " ".join(draft.lines)
    new_script["duration_requested_s"] = 27.0
    new_script["scene_duration_s"] = beat_s
    new_script["spoken_word_ceiling"] = ceiling
    new_script["writer_structure"] = script.get("writer_structure")
    new_script["human_situation"] = script.get("human_situation")
    new_script["closing_tool"] = script.get("closing_tool")
    episode["script"] = new_script
    episode["voice_paths"] = voice_paths
    episode["word_timings_per_scene"] = timings_all
    episode["scene_durations"] = [beat_s] * len(draft.lines)
    episode["duration_expected_s"] = 27.0
    episode["mode"] = "pilot_assembled_tightened_vo"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_mp4 = ROOT / "outputs/wonder_feed/clips" / f"lofi_reel_distance_silence_that_speaks_{stamp}_tight_v01.mp4"
    print(f"assembling {out_mp4}")
    mp4 = assemble_video_from_episode(episode, output_mp4=out_mp4, allow_qa_hold=True)
    episode["video_path"] = str(mp4)
    episode_out = ROOT / "outputs/wonder_feed/clips" / f"lofi_stills_distance_silence_that_speaks_{stamp}_tight_v01.json"
    save_episode_json(episode, episode_out)

    payload = {
        "ok": True,
        "before": before,
        "after": assess_lines(draft.lines, duration_s=27.0, beat_s=3.0),
        "beats": rows,
        "judge": verdict.to_dict(),
        "tts": measured,
        "video_path": str(mp4),
        "episode_json": str(episode_out),
    }
    out = OUT_DIR / "tightened_locked_01_distance.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    print(f"mp4 {mp4}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
