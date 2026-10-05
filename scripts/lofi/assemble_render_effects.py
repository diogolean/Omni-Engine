# -*- coding: utf-8 -*-
"""Assemble a LOFI episode with opt-in per-effect render gates.

Default (no flags) is the approved pipeline — every effect on.

Examples:
  python scripts/lofi/assemble_render_effects.py --fast-preview
  python scripts/lofi/assemble_render_effects.py --disable-grain
  python scripts/lofi/assemble_render_effects.py --only grain
  python scripts/lofi/assemble_render_effects.py --timing-suite
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True, encoding="utf-8-sig")

from core.economic_reel_lofi import config as lofi_cfg  # noqa: E402
from core.economic_reel_lofi.regen import (  # noqa: E402
    assemble_video_from_episode,
    load_episode_json,
)

DEFAULT_SIDECAR = ROOT / (
    "outputs/wonder_feed/clips/"
    "lofi_stills_distance_silence_that_speaks_20260827_005508_tight_v01.json"
)
OUT_DIR = ROOT / "outputs/wonder_feed/clips/effect_timing"

TIMING_SUITE: tuple[tuple[str, dict[str, object]], ...] = (
    ("preview_all_off", {"fast_preview": True}),
    ("only_grain", {"only": "grain"}),
    ("only_vignette", {"only": "vignette"}),
    ("only_kenburns", {"only": "kenburns"}),
    ("only_caption_fade", {"only": "caption_fade"}),
    ("only_pulse", {"only": "pulse"}),
    ("all_on", {}),
)

# Grain overlay + caption fade stay ON. Isolate Ken Burns / vignette / pulse.
_SHIP_BASE: dict[str, bool] = {
    "grain": True,
    "caption_fade": True,
    "kenburns": False,
    "vignette": False,
    "pulse": False,
}
SHIP_TIMING_SUITE: tuple[tuple[str, dict[str, bool]], ...] = (
    ("baseline_grain_caption", dict(_SHIP_BASE)),
    ("plus_kenburns", {**_SHIP_BASE, "kenburns": True}),
    ("plus_vignette", {**_SHIP_BASE, "vignette": True}),
    ("plus_pulse", {**_SHIP_BASE, "pulse": True}),
)
ORIGINAL_FULL_EFFECTS_BASELINE_S = 11.4 * 60.0


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="LOFI assemble with per-effect on/off gates (opt-in)."
    )
    p.add_argument(
        "--sidecar",
        type=Path,
        default=DEFAULT_SIDECAR,
        help="Episode JSON with stills + VO paths.",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output mp4 (ignored by --timing-suite).",
    )
    p.add_argument(
        "--fast-preview",
        action="store_true",
        help="Disable all gated per-frame effects. Static stills + static captions.",
    )
    p.add_argument("--disable-grain", action="store_true")
    p.add_argument("--disable-vignette", action="store_true")
    p.add_argument("--disable-kenburns", action="store_true")
    p.add_argument("--disable-caption-fade", action="store_true")
    p.add_argument("--disable-pulse", action="store_true")
    p.add_argument(
        "--only",
        choices=list(lofi_cfg.RENDER_EFFECT_KEYS),
        default=None,
        help="All effects off except this one (for isolation timing).",
    )
    p.add_argument(
        "--timing-suite",
        action="store_true",
        help="Run preview / each-effect-only / all-on and write a timing table.",
    )
    p.add_argument(
        "--timing-suite-ship",
        action="store_true",
        help=(
            "Isolation with grain+caption ON as baseline, then add Ken Burns / "
            "vignette / pulse individually. Writes production preset."
        ),
    )
    p.add_argument(
        "--preset",
        choices=["production"],
        default=None,
        help="Use the production render-effect preset.",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=float(lofi_cfg.RENDER_SUBPROCESS_TIMEOUT_S),
        help="Hard abort after this many seconds (default 300). 0 = no cap.",
    )
    p.add_argument(
        "--allow-qa-hold",
        action="store_true",
        default=True,
        help="Pilot assemble despite visual QA HOLD (default on for this diagnostic).",
    )
    return p.parse_args()


def _disable_from_args(args: argparse.Namespace) -> list[str]:
    flags = (
        ("grain", args.disable_grain),
        ("vignette", args.disable_vignette),
        ("kenburns", args.disable_kenburns),
        ("caption_fade", args.disable_caption_fade),
        ("pulse", args.disable_pulse),
    )
    return [name for name, on in flags if on]


def _run_one(
    episode: dict,
    *,
    out_mp4: Path,
    fast_preview: bool = False,
    disable: list[str] | None = None,
    only: str | None = None,
    effects: dict[str, bool] | None = None,
    preset: str | None = None,
    allow_qa_hold: bool = True,
    timeout_s: float = 300.0,
) -> dict[str, object]:
    from concurrent.futures import ThreadPoolExecutor
    from concurrent.futures import TimeoutError as FutTimeout

    from core.economic_reel_lofi.assembler import kill_descendant_ffmpeg

    fx = lofi_cfg.resolve_render_effects(
        effects,
        fast_preview=fast_preview,
        disable=disable,
        only=only,
        preset=preset,
    )
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    cap = float(timeout_s or 0.0)
    if cap > 0:
        print(f"[effect-timing] start {out_mp4.name} fx={fx} timeout={cap:.0f}s")
    else:
        print(f"[effect-timing] start {out_mp4.name} fx={fx}")
    t0 = time.perf_counter()

    def _call():
        return assemble_video_from_episode(
            episode,
            output_mp4=out_mp4,
            allow_qa_hold=allow_qa_hold,
            render_effects=fx,
            fast_preview=False,
            timeout_s=cap if cap > 0 else None,
        )

    try:
        if cap > 0:
            with ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(_call)
                try:
                    mp4 = fut.result(timeout=cap)
                except FutTimeout as exc:
                    killed = kill_descendant_ffmpeg()
                    raise TimeoutError(
                        f"assemble exceeded {cap:.0f}s "
                        f"(killed {killed} ffmpeg children)"
                    ) from exc
        else:
            mp4 = _call()
    except TimeoutError as exc:
        wall = time.perf_counter() - t0
        print(f"[effect-timing] TIMEOUT {out_mp4.name} wall={wall:.1f}s {exc}")
        raise
    wall = time.perf_counter() - t0
    rec = {
        "out": str(mp4),
        "wall_s": round(wall, 1),
        "size_bytes": int(mp4.stat().st_size) if mp4.is_file() else 0,
        "effects": fx,
        "fast_preview": bool(fast_preview),
        "only": only,
        "disable": list(disable or []),
        "timeout_s": cap,
    }
    print(f"[effect-timing] done {out_mp4.name} wall={wall:.1f}s")
    return rec


def main() -> None:
    args = _parse_args()
    sidecar = args.sidecar if args.sidecar.is_absolute() else ROOT / args.sidecar
    ep = load_episode_json(sidecar)
    if args.timing_suite:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        rows: list[dict[str, object]] = []
        for name, kwargs in TIMING_SUITE:
            rec = _run_one(
                ep,
                out_mp4=OUT_DIR / f"distance_{name}.mp4",
                allow_qa_hold=bool(args.allow_qa_hold),
                timeout_s=float(args.timeout),
                **kwargs,  # type: ignore[arg-type]
            )
            rec["config"] = name
            rows.append(rec)
        table = {
            "sidecar": str(sidecar),
            "ran_at": datetime.now(timezone.utc).isoformat(),
            "rows": rows,
        }
        report = OUT_DIR / "distance_effect_timing.json"
        report.write_text(json.dumps(table, indent=2), encoding="utf-8")
        print("[effect-timing] TABLE")
        print(f"{'config':<22} {'wall_s':>8}  effects")
        for rec in rows:
            fx = rec.get("effects") or {}
            on = ",".join(k for k, v in fx.items() if v) or "(none)"
            print(f"{rec['config']:<22} {rec['wall_s']:>8}  {on}")
        print(f"[effect-timing] wrote {report}")
        return

    if args.timing_suite_ship:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        cheap_s = float(getattr(lofi_cfg, "PRODUCTION_EFFECT_CHEAP_S", 30.0))
        rows: list[dict[str, object]] = []
        for name, fx in SHIP_TIMING_SUITE:
            rec = _run_one(
                ep,
                out_mp4=OUT_DIR / f"distance_{name}.mp4",
                effects=fx,
                allow_qa_hold=bool(args.allow_qa_hold),
                timeout_s=float(args.timeout),
            )
            rec["config"] = name
            rows.append(rec)
        by_name = {str(r["config"]): r for r in rows}
        base_wall = float(by_name["baseline_grain_caption"]["wall_s"])
        deltas = {}
        verdicts = {}
        for key, cfg in (
            ("kenburns", "plus_kenburns"),
            ("vignette", "plus_vignette"),
            ("pulse", "plus_pulse"),
        ):
            wall = float(by_name[cfg]["wall_s"])
            delta = round(wall - base_wall, 1)
            deltas[key] = delta
            verdicts[key] = "cheap" if delta < cheap_s else "expensive"
        prod = {
            "grain": True,
            "caption_fade": True,
            "kenburns": verdicts["kenburns"] == "cheap",
            "vignette": verdicts["vignette"] == "cheap",
            "pulse": verdicts["pulse"] == "cheap",
        }
        prod_rec = _run_one(
            ep,
            out_mp4=OUT_DIR / "distance_production_preset.mp4",
            effects=prod,
            allow_qa_hold=bool(args.allow_qa_hold),
            timeout_s=float(args.timeout),
        )
        prod_rec["config"] = "production_preset"
        rows.append(prod_rec)
        table = {
            "sidecar": str(sidecar),
            "ran_at": datetime.now(timezone.utc).isoformat(),
            "baseline": "grain ON + caption_fade ON; Ken Burns/vignette/pulse OFF",
            "cheap_threshold_s": cheap_s,
            "deltas_vs_baseline_s": deltas,
            "verdicts": verdicts,
            "production_preset": prod,
            "production_wall_s": prod_rec.get("wall_s"),
            "original_full_effects_baseline_s": ORIGINAL_FULL_EFFECTS_BASELINE_S,
            "rows": rows,
        }
        report = OUT_DIR / "distance_ship_effect_timing.json"
        report.write_text(json.dumps(table, indent=2), encoding="utf-8")
        preset_path = OUT_DIR / "production_preset.json"
        preset_path.write_text(json.dumps(prod, indent=2) + "\n", encoding="utf-8")
        print("[effect-timing] SHIP TABLE")
        print(f"{'config':<28} {'wall_s':>8}  delta")
        for rec in rows:
            wall = float(rec.get("wall_s") or 0)
            delta = wall - base_wall
            print(f"{rec['config']:<28} {wall:>8.1f}  {delta:+.1f}")
        print(f"verdicts={verdicts}")
        print(f"production_preset={prod} wall={prod_rec.get('wall_s')}s")
        print(
            f"vs original 11.4 min baseline "
            f"({ORIGINAL_FULL_EFFECTS_BASELINE_S:.0f}s)"
        )
        print(f"[effect-timing] wrote {report}")
        return

    disable = _disable_from_args(args)
    fx = lofi_cfg.resolve_render_effects(
        fast_preview=bool(args.fast_preview),
        disable=disable,
        only=args.only,
        preset=args.preset,
    )
    tag = "preview" if args.fast_preview else (
        "production" if args.preset == "production" else (
            f"only_{args.only}" if args.only else (
                "disable_" + "_".join(disable) if disable else "all_on"
            )
        )
    )
    out = args.out
    if out is None:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        out = OUT_DIR / f"distance_{tag}.mp4"
    elif not out.is_absolute():
        out = ROOT / out
    rec = _run_one(
        ep,
        out_mp4=out,
        fast_preview=bool(args.fast_preview),
        disable=disable,
        only=args.only,
        preset=args.preset,
        allow_qa_hold=bool(args.allow_qa_hold),
        timeout_s=float(args.timeout),
    )
    print(json.dumps(rec, indent=2))


if __name__ == "__main__":
    main()
