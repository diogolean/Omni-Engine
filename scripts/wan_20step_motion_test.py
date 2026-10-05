# -*- coding: utf-8 -*-
"""
Wan 20-step / CFG 3.5 motion test — disable LightX2V 4-step LoRA.

Same still as the static A/B clips. Soft prompt (isolate step-count fix).
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
logger = logging.getLogger("wan_20step")

SOFT_PROMPT = "camera slowly pushes in, dust particles drifting in the light"
STILL = Path(
    "outputs/ancient_knowledge/wan_reel_tests/"
    "run_20260810_055504/stills/wan_still_01_20260810_055517.png"
)
OUT_DIR = coerce_outputs_path("outputs/ancient_knowledge/wan_reel_tests/motion_ab_20260810")


def main() -> int:
    if not STILL.is_file():
        print(f"ERROR: still missing: {STILL}", file=sys.stderr)
        return 2

    os.environ["ENABLE_REMOTE_GPU_WORKFLOWS"] = "true"
    os.environ["REMOTE_GPU_MODE"] = "runpod"
    os.environ["REMOTE_GPU_TIMEOUT_S"] = "1800"

    from agents.mcp.model_api_flows import apply_production_flow, resolve_production_flow
    from core.remote_gpu_manager import get_manager, reset_manager
    from scripts.wan_motion_ab_test import _frame_motion_score

    flow = resolve_production_flow(preset_name="remote_gpu_serverless")
    apply_production_flow(flow, explicit=True)
    reset_manager()
    mgr = get_manager()
    # Client wait must exceed worker; endpoint allows 1800s.
    mgr.client.timeout_s = 1800.0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "test_20step_cfg35_soft_prompt.mp4"

    # Official fix: disable LightX2V → Switch picks 20 steps + CFG 3.5
    patches = {"129:131": {"value": False}}
    # Custom handler hard-fails at 600s ("não terminou em 600s") regardless of
    # endpoint executionTimeoutMs=1800s / job timeout hints. 7s@512x896 20-step
    # lands ~602s; use 5s (~71% frames) to clear the cap while keeping res.
    duration_s = float(os.environ.get("WAN_20STEP_DURATION_S", "5"))
    logger.info(
        "20-step test | disable LightX2V | duration_s=%.1f | patches=%s | prompt=%r | still=%s",
        duration_s, patches, SOFT_PROMPT, STILL.name,
    )
    path = mgr.generate_video(
        STILL,
        prompt=SOFT_PROMPT,
        output_path=out,
        duration_s=duration_s,
        width=512,
        height=896,
        stem="test_20step_cfg35",
        extra_patches=patches,
    )
    path = Path(path)
    gpu_s = float(getattr(mgr.client, "last_job_seconds", 0) or 0)
    motion = _frame_motion_score(path)
    report = {
        "label": "test_20step_cfg35_soft_prompt",
        "path": str(path),
        "prompt": SOFT_PROMPT,
        "patches": patches,
        "duration_s_requested": duration_s,
        "note": (
            "Enable 4steps LoRA?=False → 20 steps, CFG 3.5, default shift=5. "
            "Duration shortened from 7s→5s to fit custom worker 600s hard cap "
            "(7s 20-step timed out at ~602s twice)."
        ),
        "gpu_seconds": gpu_s,
        "baseline_first_last_diff": 4.06,
        "test_a_first_last_diff": 4.78,
        **motion,
    }
    (OUT_DIR / "motion_20step_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
