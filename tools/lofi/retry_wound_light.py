# -*- coding: utf-8 -*-
"""One loosened-budget retry of wound_light from the ORIGINAL lines. No 3rd try."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=True, encoding="utf-8-sig")

from agents.writer.freeform_writer import ScriptDraft, split_long_line  # noqa: E402
from agents.writer.judge_gate import judge_draft  # noqa: E402
from agents.writer.spoken_budget import (  # noqa: E402
    apply_draft_to_script,
    assess_lines,
    count_words,
    enforce_spoken_budget,
    missing_keep_terms,
    rewrite_single_line,
    script_to_draft,
)
from agents.writer.writer_brief import WriterBrief  # noqa: E402
from core.economic_reel_lofi import config as lofi_cfg  # noqa: E402

LOCKED = ROOT / "outputs/wonder_feed/clips/freeform_drafts/locked_freeform_05_The_wound_is_the_place_w.json"
OUT = ROOT / "outputs/wonder_feed/clips/freeform_drafts/tightened_locked_05_wound_light_retry.json"

BEAT_S = 4.0  # floor(4/60*150)+2 = 12 word ceiling
DURATION_S = 40.0
MUST_KEEP: dict[int, list[str]] = {
    0: ["sleeve"],
    1: ["bandage"],
    2: ["light"],
    3: ["wound"],
    4: ["sleeve", "light"],
    5: ["light"],
    6: ["wound"],
    7: ["sleeve"],
    8: [],
    9: ["light"],
}


def main() -> int:
    raw = json.loads(LOCKED.read_text(encoding="utf-8"))
    script = dict(raw["script"] if isinstance(raw.get("script"), dict) else raw)
    script["theme"] = script.get("theme") or "wound"
    script["subtheme"] = script.get("subtheme") or "the_wound_is_the_place"
    script["scene_duration_s"] = BEAT_S
    script["duration_requested_s"] = DURATION_S
    rows = [r for r in (script.get("lines") or []) if isinstance(r, dict)]
    for row in rows:
        row["duration_s"] = BEAT_S
        row["spoken_word_ceiling"] = lofi_cfg.beat_word_ceiling(BEAT_S)

    brief = WriterBrief.from_theme(
        theme=str(script.get("theme") or "wound"),
        subtheme=str(script.get("subtheme") or ""),
        module="relationship",
        meta={"duration_s": DURATION_S, "beat_duration_s": BEAT_S},
    )
    draft = ScriptDraft(
        lines=[str(r.get("text") or "") for r in rows],
        human_situation=str(script.get("human_situation") or ""),
        structure=str(script.get("writer_structure") or ""),
        closing_tool=str(script.get("closing_tool") or ""),
        brief=brief,
        provider="freeform_v1",
        meta=dict(brief.meta),
    )
    before = assess_lines(draft.lines, duration_s=DURATION_S, beat_s=BEAT_S)
    ceiling = lofi_cfg.beat_word_ceiling(BEAT_S)
    print(f"wound_light retry ceiling={ceiling}w / {BEAT_S:.1f}s  before_ok={before.get('ok')}")
    draft, budget = enforce_spoken_budget(draft, must_keep=MUST_KEEP)
    print(f"after_ok={budget.get('ok')} {budget.get('reason')}")
    if not budget.get("ok"):
        OUT.write_text(
            json.dumps({"ok": False, "stop_reason": budget.get("reason"), "after": budget}, indent=2)
            + "\n",
            encoding="utf-8",
        )
        print("FAIL budget")
        return 2

    verdict = judge_draft(draft)
    print("judge", verdict.summary())
    apply_draft_to_script(script, draft)
    script["judge"] = verdict.to_dict()
    script["spoken_budget"] = budget
    script["spoken_budget_ok"] = bool(budget.get("ok"))
    script["hook_variety"] = {
        "angle": "three-quarter view",
        "gaze": "gaze out of frame",
        "palette_key": "CONTRAST",
    }
    if script.get("lines"):
        script["lines"][0]["hook_angle"] = "three-quarter view"
        script["lines"][0]["hook_gaze"] = "gaze out of frame"
        script["lines"][0]["palette_key"] = "CONTRAST"

    if not verdict.ok:
        payload = {
            "ok": False,
            "pack": "wound_light",
            "stop_reason": "judge failed on loosened retry — drop from batch",
            "before": before,
            "after": budget,
            "judge": verdict.to_dict(),
            "script": script,
            "drop_from_batch": True,
        }
        OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print("DROP wound_light")
        return 3

    # TTS vs 4.0s slots
    from agents.media.audio_engine import generate_voiceover_with_timestamps
    from core.economic_reel_lofi.assembler import measure_vo_speech_duration
    from core.economic_reel_lofi.pipeline import (
        _sanitize_caption_typos,
        _tts_breath_commas,
        _tts_text_with_breaks,
    )
    import tempfile

    work = Path(tempfile.gettempdir()) / "lofi_wound_retry_tts"
    work.mkdir(parents=True, exist_ok=True)
    measured = []
    for i, line in enumerate(list(draft.lines)):
        caption = _sanitize_caption_typos(line)
        vo_path = work / f"vo_{i + 1:02d}.mp3"
        tts_text = _tts_text_with_breaks(_tts_breath_commas(caption))
        vo_path, _raw = generate_voiceover_with_timestamps(
            tts_text,
            vo_path,
            voice_id=lofi_cfg.tts_voice_id() or None,
            model_id=lofi_cfg.tts_model(),
            force_elevenlabs=True,
            expressive_mode=False,
            enable_ssml="<break" in tts_text,
            speed=lofi_cfg.tts_speed(),
            voice_settings={
                "stability": 1.0,
                "similarity_boost": 1.0,
                "style": 0.0,
                "use_speaker_boost": True,
                "speed": lofi_cfg.tts_speed(),
            },
        )
        vo_dur = float(measure_vo_speech_duration(vo_path)) if vo_path.is_file() else 0.0
        declared = BEAT_S
        if lofi_cfg.vo_duration_overrun(vo_dur, duration_s=declared):
            keep = MUST_KEEP.get(i) or []
            tts_ceiling = max(len(keep) + 2, min(ceiling - 2, count_words(caption) - 2))
            try:
                caption = rewrite_single_line(
                    caption,
                    ceiling=tts_ceiling,
                    theme="wound",
                    subtheme="the_wound_is_the_place",
                    neighbor_before=draft.lines[i - 1] if i > 0 else "",
                    neighbor_after=draft.lines[i + 1] if i + 1 < len(draft.lines) else "",
                    must_keep=keep,
                )
                draft.lines[i] = caption
                tts_text = _tts_text_with_breaks(_tts_breath_commas(caption))
                vo_path, _raw = generate_voiceover_with_timestamps(
                    tts_text,
                    vo_path,
                    voice_id=lofi_cfg.tts_voice_id() or None,
                    model_id=lofi_cfg.tts_model(),
                    force_elevenlabs=True,
                    expressive_mode=False,
                    enable_ssml="<break" in tts_text,
                    speed=lofi_cfg.tts_speed(),
                    voice_settings={
                        "stability": 1.0,
                        "similarity_boost": 1.0,
                        "style": 0.0,
                        "use_speaker_boost": True,
                        "speed": lofi_cfg.tts_speed(),
                    },
                )
                vo_dur = float(measure_vo_speech_duration(vo_path)) if vo_path.is_file() else 0.0
            except Exception as exc:  # noqa: E402
                print(f"TTS rewrite fail beat {i + 1}: {exc}")
        rec = {
            "scene": i + 1,
            "text": caption,
            "words": count_words(caption),
            "vo_dur_s": round(vo_dur, 3),
            "declared_s": declared,
            "overrun": lofi_cfg.vo_duration_overrun(vo_dur, duration_s=declared),
        }
        measured.append(rec)
        print(f"  beat {i + 1}: {vo_dur:.2f}s / {declared:.1f}s {'OVER' if rec['overrun'] else 'ok'} {caption!r}")

    overruns = [
        {"index": i, "vo_dur": r["vo_dur_s"], "duration_s": BEAT_S, "tightened": True}
        for i, r in enumerate(measured)
        if r["overrun"]
    ]
    tts_stop = None
    if overruns:
        applied, tts_stop = lofi_cfg.apply_isolated_tts_duration_bumps(
            overruns, requested_total_s=DURATION_S, n_beats=len(draft.lines)
        )
        if not tts_stop:
            for note in applied:
                idx = int(note["index"])
                measured[idx]["declared_s"] = float(note["bumped_s"])
                measured[idx]["overrun"] = lofi_cfg.vo_duration_overrun(
                    measured[idx]["vo_dur_s"], duration_s=float(note["bumped_s"])
                )
                script["lines"][idx]["duration_s"] = float(note["bumped_s"])
            leftover = [r for r in measured if r["overrun"]]
            if leftover:
                tts_stop = f"TTS overrun remains {[r['scene'] for r in leftover]}"

    apply_draft_to_script(script, draft)
    for i, rec in enumerate(measured):
        script["lines"][i]["duration_s"] = rec["declared_s"]
        script["lines"][i]["tts_vo_dur_s"] = rec["vo_dur_s"]
        script["lines"][i]["caption_beats"] = split_long_line(
            rec["text"], *lofi_cfg.thematic_caption_limits()
        )

    ok = verdict.ok and not tts_stop
    payload = {
        "ok": ok,
        "pack": "wound_light",
        "stop_reason": tts_stop,
        "before": before,
        "after": assess_lines(draft.lines, duration_s=DURATION_S, beat_s=BEAT_S),
        "judge": verdict.to_dict(),
        "tts": measured,
        "script": script,
        "drop_from_batch": not ok,
        "loosened": {"beat_s": BEAT_S, "ceiling": ceiling, "duration_s": DURATION_S},
    }
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    prod = ROOT / "outputs/wonder_feed/clips/freeform_drafts/prod_05_wound_light.json"
    if ok:
        prod.write_text(json.dumps({"script": script}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"PASS wrote {prod}")
    else:
        print(f"FAIL {tts_stop}")
    print(f"wrote {OUT}")
    return 0 if ok else 3


if __name__ == "__main__":
    raise SystemExit(main())
