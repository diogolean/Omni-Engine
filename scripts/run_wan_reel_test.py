# -*- coding: utf-8 -*-
"""
WAN_REEL test render (no publish).

Default: serverless (runpod) — 10 scenes × fixed 7 s = 70 s video.
Smoke: ``--scenes 1 --video-only`` — Flux still → Wan first-frame → one clip.

Usage
-----
  python scripts/run_wan_reel_test.py --scenes 1 --video-only
  python scripts/run_wan_reel_test.py --mode runpod --topic "Göbekli Tepe"
  python scripts/run_wan_reel_test.py --mode comfyui   # needs live pod proxy
"""
from __future__ import annotations

from pathlib import Path as _ReorgPath
import sys as _reorg_sys
_REORG_ROOT = _ReorgPath(__file__).resolve().parents[1]
if str(_REORG_ROOT) not in _reorg_sys.path:
    _reorg_sys.path.insert(0, str(_REORG_ROOT))

import argparse
import json
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("wan_reel_test")


def main() -> int:
    p = argparse.ArgumentParser(description="WAN_REEL test render")
    p.add_argument(
        "--topic",
        default=(
            "Göbekli Tepe — the 12,000-year-old temple that rewrote human history"
        ),
    )
    p.add_argument("--page", default="ancient_knowledge")
    p.add_argument(
        "--mode",
        choices=("runpod", "comfyui"),
        default="runpod",
        help="Remote GPU transport (default: runpod serverless).",
    )
    p.add_argument("--video-length", type=float, default=70.0)
    p.add_argument(
        "--scene-duration",
        default="fixed:7",
        help="Fixed Wan clip length (never progressive). Default fixed:7",
    )
    p.add_argument("--scenes", type=int, default=10, help="Scene count (default 10)")
    p.add_argument(
        "--motion-prompt",
        default=None,
        help="Optional motion prompt override (useful for --scenes 1 smoke).",
    )
    p.add_argument(
        "--video-only",
        action="store_true",
        help="Skip F5 narration + audio mux (Flux still → Wan clip only).",
    )
    p.add_argument(
        "--model-api-flow",
        default=None,
        help="Optional preset (default: remote_gpu_serverless if mode=runpod)",
    )
    p.add_argument("--out-dir", type=Path, default=None)
    args = p.parse_args()

    if not str(args.scene_duration).lower().startswith("fixed:"):
        print(
            "ERROR: WAN video blocks require fixed:N scene_duration "
            f"(got {args.scene_duration!r}). Progressive is for ECONOMIC stills only.",
            file=sys.stderr,
        )
        return 2

    os.environ["ENABLE_REMOTE_GPU_WORKFLOWS"] = "true"
    os.environ["REMOTE_GPU_MODE"] = args.mode
    os.environ["ACTIVE_PAGE"] = args.page
    # Wan 7s can exceed the old 600s default on cold flex workers.
    os.environ.setdefault("REMOTE_GPU_TIMEOUT_S", "1800")

    from agents.mcp.model_api_flows import apply_production_flow, resolve_production_flow
    from core.remote_gpu_manager import reset_manager
    from core.wan_reel_engine import run_wan_reel_test

    preset = args.model_api_flow or (
        "remote_gpu_serverless" if args.mode == "runpod" else "remote_gpu_pod"
    )
    flow = resolve_production_flow(preset_name=preset)
    apply_production_flow(flow, explicit=True)
    reset_manager()
    logger.info("MODEL_API_FLOW | %s", flow.summary_line())

    motion = args.motion_prompt
    if motion is None and int(args.scenes) == 1:
        motion = (
            "camera slowly pushes in, dust particles drifting in the light"
        )

    report = run_wan_reel_test(
        args.topic,
        page_id=args.page,
        video_length_s=float(args.video_length),
        scene_duration=str(args.scene_duration),
        out_dir=args.out_dir,
        max_scenes=int(args.scenes),
        motion_prompt_override=motion,
        video_only=bool(args.video_only),
    )

    # Console cost report (same spirit as image/audio comparison)
    video_clips = [c for c in report.clips if c.kind == "video"]
    image_clips = [c for c in report.clips if c.kind == "image"]
    audio_clips = [c for c in report.clips if c.kind == "audio"]
    print("\n" + "=" * 64)
    print("WAN_REEL TEST COST REPORT")
    print("=" * 64)
    print(f"  Topic              : {report.topic}")
    print(f"  Mode               : {report.mode}")
    print(f"  Plan               : {report.n_scenes} × {report.scene_duration_s:.0f}s "
          f"= {report.video_length_s:.0f}s")
    print(f"  Narration          : {report.narration_words} words / "
          f"{report.voice_duration_s:.1f}s VO")
    print("-" * 64)
    for c in video_clips:
        print(
            f"  Wan scene {c.scene_index + 1:02d}     : "
            f"{c.gpu_seconds:7.1f}s GPU  ({c.gpu_seconds / max(0.1, c.duration_s):.2f}× "
            f"realtime for {c.duration_s:.0f}s clip)"
        )
        print(f"                     : {c.path}")
    img_gpu = sum(c.gpu_seconds for c in image_clips)
    vid_gpu = sum(c.gpu_seconds for c in video_clips)
    aud_gpu = sum(c.gpu_seconds for c in audio_clips)
    print("-" * 64)
    print(f"  Image GPU total    : {img_gpu:7.1f}s")
    print(f"  Wan GPU total      : {vid_gpu:7.1f}s")
    print(f"  F5 audio GPU total : {aud_gpu:7.1f}s")
    print(f"  ALL GPU total      : {report.total_gpu_seconds:7.1f}s")
    print(f"  Est. RunPod USD    : ${report.total_gpu_usd:.4f}")
    print(
        f"  Together Wan 2.7   : ${report.together_wan27_usd_at_0_10_per_s:.2f} "
        f"(${0.10:.2f}/s × {report.video_length_s:.0f}s output video only)"
    )
    print(f"  Output MP4         : {report.output_mp4}")
    print(f"  Report JSON        : {Path(report.output_mp4).parent / 'wan_reel_report.json'}")
    print("=" * 64)
    print(json.dumps({
        "total_gpu_seconds": report.total_gpu_seconds,
        "wan_gpu_seconds": vid_gpu,
        "avg_gpu_s_per_7s_clip": (vid_gpu / len(video_clips)) if video_clips else 0,
        "est_runpod_usd": report.total_gpu_usd,
        "together_wan27_usd": report.together_wan27_usd_at_0_10_per_s,
        "output_mp4": report.output_mp4,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
