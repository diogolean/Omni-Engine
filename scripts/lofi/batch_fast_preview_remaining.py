# -*- coding: utf-8 -*-
"""Fast-preview assemble remaining locked freeform packs (no still regen)."""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.pipeline_paths import page_outputs_dir

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True, encoding="utf-8-sig")

from core.economic_reel_lofi import config as lofi_cfg  # noqa: E402
from core.economic_reel_lofi.assembler import measure_vo_speech_duration  # noqa: E402
from core.economic_reel_lofi.pipeline import (  # noqa: E402
    _render_one_voiceover,
    _sanitize_caption_typos,
)
from core.economic_reel_lofi.regen import (  # noqa: E402
    assemble_video_from_episode,
    load_episode_json,
    save_episode_json,
)

PACKS: tuple[tuple[str, str, str], ...] = (
    (
        "forgiveness",
        "outputs/wonder_feed/clips/lofi_stills_forgiveness_the_apology_that_never_came_20260826_223328_v02.json",
        "outputs/wonder_feed/clips/freeform_drafts/locked_freeform_02_forgiveness.json",
    ),
    (
        "starting_over",
        "outputs/wonder_feed/clips/lofi_stills_starting_over_after_it_ended_20260826_224216_v03.json",
        "outputs/wonder_feed/clips/freeform_drafts/locked_freeform_03_starting_over.json",
    ),
    (
        "suffer_imagination",
        "outputs/wonder_feed/clips/lofi_stills_hope_20260826_225023_v04.json",
        "outputs/wonder_feed/clips/freeform_drafts/locked_freeform_04_We_suffer_more_often_in.json",
    ),
    (
        "wound_light",
        "outputs/wonder_feed/clips/lofi_stills_hope_20260826_225946_v05.json",
        "outputs/wonder_feed/clips/freeform_drafts/locked_freeform_05_The_wound_is_the_place_w.json",
    ),
)

OUT_DIR = page_outputs_dir("wonder_feed") / "clips"
ASSEMBLE_TIMEOUT_FLOOR_S = float(lofi_cfg.RENDER_SUBPROCESS_TIMEOUT_S)
TTS_TIMEOUT_S = 900.0


def _hold_beats(episode: dict) -> list[str]:
    flags = [str(x) for x in (episode.get("visual_qa_flags") or [])]
    leftover = []
    for f in flags:
        leftover.append(f.split(":")[0].strip() if ":" in f else f)
    return leftover


def _ensure_vo_extend_slots(episode: dict) -> None:
    """Generate missing VO and size each slot to the spoken file. Preview only."""
    from concurrent.futures import ThreadPoolExecutor

    script = episode.get("script") if isinstance(episode.get("script"), dict) else {}
    lines = list((script or {}).get("lines") or [])
    work_dir = Path(str(episode.get("work_dir") or "."))
    work_dir.mkdir(parents=True, exist_ok=True)
    existing = list(episode.get("voice_paths") or [])
    n = len(lines)
    captions = [
        _sanitize_caption_typos(str((ln or {}).get("text") or "")) for ln in lines
    ]
    vo_paths: list[Path | None] = []
    missing: list[tuple[int, str, Path]] = []
    for i, caption in enumerate(captions):
        prior = Path(str(existing[i])) if i < len(existing) and existing[i] else None
        vo_path = (
            prior
            if prior and prior.is_file()
            else work_dir / f"vo_preview_scene_{i + 1:02d}.mp3"
        )
        vo_paths.append(vo_path)
        if (not vo_path.is_file()) and caption.strip():
            missing.append((i, caption, vo_path))
    if missing:
        n_workers = min(4, len(missing))
        print(f"[batch-preview] TTS n={len(missing)} workers={n_workers}")
        from concurrent.futures import wait, FIRST_COMPLETED

        with ThreadPoolExecutor(max_workers=n_workers) as ex:
            futs = {
                ex.submit(_render_one_voiceover, cap, path): i
                for i, cap, path in missing
            }
            pending = set(futs)
            deadline = time.perf_counter() + TTS_TIMEOUT_S
            while pending:
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    raise TimeoutError(f"TTS exceeded {TTS_TIMEOUT_S:.0f}s")
                done, pending = wait(
                    pending, timeout=remaining, return_when=FIRST_COMPLETED
                )
                if not done:
                    raise TimeoutError(f"TTS exceeded {TTS_TIMEOUT_S:.0f}s")
                for fut in done:
                    i = futs[fut]
                    vo_path, timings, _dur = fut.result()
                    vo_paths[i] = vo_path
                    wts = list(episode.get("word_timings_per_scene") or [])
                    while len(wts) < n:
                        wts.append(None)
                    wts[i] = timings
                    episode["word_timings_per_scene"] = wts
    durations: list[float] = []
    for i, ln in enumerate(lines):
        vp = vo_paths[i]
        vo_dur = 0.0
        if vp and Path(vp).is_file():
            try:
                vo_dur = float(measure_vo_speech_duration(vp))
            except Exception:  # noqa: BLE001
                vo_dur = 0.0
        declared = float((ln or {}).get("duration_s") or lofi_cfg.beat_duration_s())
        trail = 0.0 if i >= n - 1 else float(getattr(lofi_cfg, "VO_INTERLINE_SILENCE_S", 0.30))
        slot, _ = lofi_cfg.slot_duration_for_vo(
            vo_dur, base_s=max(declared, vo_dur), trailing_silence_s=trail
        )
        if isinstance(ln, dict):
            ln["duration_s"] = float(slot)
        durations.append(float(slot))
        print(f"[batch-preview] beat={i + 1} vo={vo_dur:.2f}s slot={slot:.2f}s")
    episode["voice_paths"] = [str(p) if p else None for p in vo_paths]
    episode["scene_durations"] = durations


def _assemble_timeout_s(episode: dict) -> float:
    durs = episode.get("scene_durations") or []
    total = float(sum(float(x) for x in durs)) if durs else 30.0
    return max(ASSEMBLE_TIMEOUT_FLOOR_S, min(900.0, 12.0 * total))


def main() -> None:
    rows: list[dict] = []
    for name, stills_rel, locked_rel in PACKS:
        t0 = time.perf_counter()
        rec: dict = {"episode": name, "ok": False}
        try:
            stills = ROOT / stills_rel
            locked = ROOT / locked_rel
            ep = load_episode_json(stills)
            locked_doc = json.loads(locked.read_text(encoding="utf-8"))
            locked_script = locked_doc.get("script") if isinstance(locked_doc.get("script"), dict) else locked_doc
            if isinstance(locked_script, dict) and locked_script.get("lines"):
                ep_script = ep.get("script") if isinstance(ep.get("script"), dict) else {}
                ep_script = dict(ep_script)
                ep_script["lines"] = list(locked_script.get("lines") or [])
                ep_script["theme"] = locked_script.get("theme") or ep_script.get("theme")
                ep_script["subtheme"] = locked_script.get("subtheme") or ep_script.get("subtheme")
                ep_script["monologue"] = locked_script.get("monologue") or ep_script.get("monologue")
                ep["script"] = ep_script
            holds = _hold_beats(ep)
            rec["hold_beats"] = holds
            rec["ship_ok"] = bool(ep.get("ship_ok"))
            _ensure_vo_extend_slots(ep)
            cap = _assemble_timeout_s(ep)
            out_mp4 = OUT_DIR / f"lofi_reel_{name}_fast_preview.mp4"
            print(f"[batch-preview] assemble {name} timeout={cap:.0f}s -> {out_mp4.name}")
            from concurrent.futures import ThreadPoolExecutor
            from concurrent.futures import TimeoutError as FutTimeout
            from core.economic_reel_lofi.assembler import kill_descendant_ffmpeg

            def _call():
                return assemble_video_from_episode(
                    ep,
                    output_mp4=out_mp4,
                    allow_qa_hold=True,
                    fast_preview=True,
                    timeout_s=cap,
                )

            with ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(_call)
                try:
                    mp4 = fut.result(timeout=cap)
                except FutTimeout as exc:
                    killed = kill_descendant_ffmpeg()
                    raise TimeoutError(
                        f"assemble exceeded {cap:.0f}s (killed {killed} ffmpeg)"
                    ) from exc
            ep["video_path"] = str(mp4)
            ep["mode"] = "fast_preview_batch"
            sidecar = out_mp4.with_suffix(".json")
            save_episode_json(ep, sidecar)
            rec.update(
                {
                    "ok": True,
                    "mp4": str(mp4),
                    "sidecar": str(sidecar),
                    "runtime_s": round(time.perf_counter() - t0, 1),
                    "size_bytes": int(mp4.stat().st_size),
                    "declared_s": round(sum(float(x) for x in (ep.get("scene_durations") or [])), 2),
                    "ship_ok": False,
                    "hold_beats": holds,
                    "n_holds": len(holds),
                }
            )
            print(f"[batch-preview] DONE {name} wall={rec['runtime_s']}s holds={len(holds)}")
        except Exception as exc:  # noqa: BLE001
            rec["error"] = f"{type(exc).__name__}: {exc}"
            rec["runtime_s"] = round(time.perf_counter() - t0, 1)
            print(f"[batch-preview] FAIL {name} {rec['error']}")
        rows.append(rec)
    report = {
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "rows": rows,
    }
    out = OUT_DIR / "effect_timing" / "batch_fast_preview_remaining.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("[batch-preview] TABLE")
    print(f"{'episode':<22} {'ok':<5} {'wall_s':>8} {'holds':>6} ship_ok")
    for rec in rows:
        print(
            f"{rec.get('episode',''):<22} {str(rec.get('ok')):<5} "
            f"{rec.get('runtime_s', 0):>8} {rec.get('n_holds', len(rec.get('hold_beats') or [])):>6} "
            f"{rec.get('ship_ok')}"
        )
    print(f"[batch-preview] wrote {out}")


if __name__ == "__main__":
    main()
