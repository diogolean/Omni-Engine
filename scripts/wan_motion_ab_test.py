# -*- coding: utf-8 -*-
"""
Isolated Wan motion diagnosis — same still, two clips.

Test A: strong cinematography prompt, unchanged workflow params.
Test B: soft prompt (same as failed smoke), raise ModelSamplingSD3.shift 5→8.
"""
from __future__ import annotations

from pathlib import Path as _ReorgPath
import sys as _reorg_sys
_REORG_ROOT = _ReorgPath(__file__).resolve().parents[1]
if str(_REORG_ROOT) not in _reorg_sys.path:
    _reorg_sys.path.insert(0, str(_REORG_ROOT))

import json
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils.pipeline_paths import coerce_outputs_path
os.chdir(ROOT)

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("wan_motion_ab")

SOFT_PROMPT = (
    "camera slowly pushes in, dust particles drifting in the light"
)
STRONG_PROMPT = (
    "aggressive fast dolly-in, camera whip pan left to right, dust violently "
    "swirling in the light shafts, dramatic parallax between foreground "
    "columns and background pyramid"
)

# ModelSamplingSD3 nodes in wan2_2_img_to_video.json (high + low noise)
_SHIFT_NODES = ("129:103", "129:104")
_FOUR_STEP_BOOL = "129:131"


def _frame_motion_score(mp4: Path, sample_n: int = 8) -> dict:
    import numpy as np
    from moviepy import VideoFileClip
    from PIL import Image

    with VideoFileClip(str(mp4)) as clip:
        dur = float(clip.duration)
        fps = float(clip.fps or 16)
        wh = list(clip.size)
        times = [0.0] + [
            dur * i / (sample_n - 1) for i in range(1, sample_n - 1)
        ] + [max(0.0, dur - 1.0 / max(fps, 1))]
        frames = []
        for t in times:
            fr = clip.get_frame(min(t, max(0.0, dur - 1e-3)))
            frames.append(np.asarray(fr, dtype=np.float32))
        diffs = []
        for a, b in zip(frames, frames[1:]):
            diffs.append(float(np.mean(np.abs(a - b))))
        first_last = float(np.mean(np.abs(frames[0] - frames[-1])))
        # Save first/last for quick visual check
        out_dir = mp4.parent
        Image.fromarray(frames[0].astype("uint8")).save(out_dir / f"{mp4.stem}_f000.png")
        Image.fromarray(frames[-1].astype("uint8")).save(out_dir / f"{mp4.stem}_fend.png")
    return {
        "duration_s": round(dur, 3),
        "fps": fps,
        "size": wh,
        "mean_frame_diff": round(float(np.mean(diffs)), 4),
        "max_frame_diff": round(float(np.max(diffs)), 4),
        "first_last_diff": round(first_last, 4),
        "bytes": mp4.stat().st_size,
    }


def _run_one(
    mgr,
    still: Path,
    *,
    label: str,
    prompt: str,
    out: Path,
    extra_patches: dict | None = None,
) -> dict:
    logger.info("=== %s === prompt=%r patches=%s", label, prompt[:80], extra_patches)
    path = mgr.generate_video(
        still,
        prompt=prompt,
        output_path=out,
        duration_s=7.0,
        width=512,
        height=896,
        stem=label,
        extra_patches=extra_patches,
    )
    path = Path(path)
    gpu_s = float(getattr(mgr.client, "last_job_seconds", 0) or 0)
    motion = _frame_motion_score(path)
    return {
        "label": label,
        "path": str(path),
        "prompt": prompt,
        "extra_patches": extra_patches or {},
        "gpu_seconds": gpu_s,
        **motion,
    }


def main() -> int:
    still = Path(
        os.environ.get(
            "WAN_AB_STILL",
            "outputs/ancient_knowledge/wan_reel_tests/"
            "run_20260810_055504/stills/wan_still_01_20260810_055517.png",
        )
    )
    if not still.is_file():
        print(f"ERROR: still missing: {still}", file=sys.stderr)
        return 2

    os.environ["ENABLE_REMOTE_GPU_WORKFLOWS"] = "true"
    os.environ["REMOTE_GPU_MODE"] = "runpod"
    os.environ.setdefault("REMOTE_GPU_TIMEOUT_S", "1800")

    from agents.mcp.model_api_flows import apply_production_flow, resolve_production_flow
    from core.remote_gpu_manager import get_manager, reset_manager

    flow = resolve_production_flow(preset_name="remote_gpu_serverless")
    apply_production_flow(flow, explicit=True)
    reset_manager()
    mgr = get_manager()

    out_dir = coerce_outputs_path("outputs/ancient_knowledge/wan_reel_tests/motion_ab_20260810")
    out_dir.mkdir(parents=True, exist_ok=True)

    # Test A — prompt only
    report_a = _run_one(
        mgr,
        still,
        label="test_a_strong_prompt",
        prompt=STRONG_PROMPT,
        out=out_dir / "test_a_strong_prompt.mp4",
        extra_patches=None,
    )

    # Test B — raise flow-shift; soft prompt (failed-smoke wording)
    shift_patches = {nid: {"shift": 8.0} for nid in _SHIFT_NODES}
    report_b = _run_one(
        mgr,
        still,
        label="test_b_shift8_soft_prompt",
        prompt=SOFT_PROMPT,
        out=out_dir / "test_b_shift8_soft_prompt.mp4",
        extra_patches=shift_patches,
    )

    summary = {
        "still": str(still),
        "workflow_defaults_note": {
            "fps": 16,
            "resolution_injected": "512x896",
            "enable_4steps_lora": True,
            "cfg_4step": 1.0,
            "cfg_20step": 3.5,
            "model_sampling_shift_default": 5.0,
            "test_b_shift": 8.0,
        },
        "test_a": report_a,
        "test_b": report_b,
    }
    (out_dir / "motion_ab_report.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
