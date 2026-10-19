# -*- coding: utf-8 -*-
"""
Isolate whether ComfySwitchNode is dropping the LightX2V 4-step path.

1-4: dump the SUBMITTED prompt (KSampler never sees a literal 4 — only a
     switch link). RunPod does not return per-node Comfy execution logs.
5:   one 5s clip with steps=4 / cfg=1 / LoRA models hardcoded on the
     samplers, bypassing every ComfySwitchNode.

Usage
-----
    python scripts/wan_hardcode_4step_test.py --dump-only
    python scripts/wan_hardcode_4step_test.py --run
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

from utils.pipeline_paths import page_outputs_dir
os.chdir(ROOT)

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("wan_hardcode_4step")

OUT = page_outputs_dir("ancient_knowledge") / "video_clip_benchmarks" / "hardcode_4step_5s"
STILL = ROOT / (
    "outputs/ancient_knowledge/assets/ep_20260815_1830/work/"
    "london_hammer_encased_in_545102_v01_act16_20260815_213612.png"
)
PROMPT = (
    "Cinematic documentary live-action motion, mysterious ancient-world tone. "
    "CAMERA: slow dolly-in toward the subject. Moderate speed, no whip pans."
)
USD_PER_S = 0.00031

# Bypass ComfySwitchNode: literal sampler params + LoRA UNets directly.
# 129:86 = high-noise KSampler, 129:85 = low-noise KSampler.
# 129:104 / 129:103 = ModelSamplingSD3 wrapping LoRA-patched UNets.
# 129:101 / 129:102 = LightX2V 4-step LoRA loaders.
HARDCODE_4STEP_PATCHES = {
    "129:86": {
        "steps": 4,
        "cfg": 1.0,
        "start_at_step": 0,
        "end_at_step": 2,
        "sampler_name": "euler",
        "scheduler": "simple",
    },
    "129:85": {
        "steps": 4,
        "cfg": 1.0,
        "start_at_step": 2,
        "end_at_step": 4,
        "sampler_name": "euler",
        "scheduler": "simple",
    },
    "129:104": {"model": ["129:101", 0]},  # high ModelSampling ← high LoRA
    "129:103": {"model": ["129:102", 0]},  # low ModelSampling ← low LoRA
}


def _sampler_snapshot(wf: dict) -> dict:
    out = {"ksamplers": {}, "switches": {}, "boolean": {}, "loras": {}}
    for nid, node in wf.items():
        if not isinstance(node, dict):
            continue
        ct = node.get("class_type")
        inp = node.get("inputs") or {}
        title = str((node.get("_meta") or {}).get("title") or "")
        if ct == "KSamplerAdvanced":
            out["ksamplers"][nid] = {
                "steps": inp.get("steps"),
                "cfg": inp.get("cfg"),
                "start_at_step": inp.get("start_at_step"),
                "end_at_step": inp.get("end_at_step"),
                "model": inp.get("model"),
                "steps_is_literal": not isinstance(inp.get("steps"), list),
            }
        elif ct == "ComfySwitchNode":
            out["switches"][nid] = {
                "title": title,
                "switch": inp.get("switch"),
                "on_true": inp.get("on_true"),
                "on_false": inp.get("on_false"),
            }
        elif ct == "PrimitiveBoolean":
            out["boolean"][nid] = {"title": title, "value": inp.get("value")}
        elif ct == "LoraLoaderModelOnly":
            out["loras"][nid] = {
                "lora_name": inp.get("lora_name"),
                "model": inp.get("model"),
            }
    return out


def _prep_mgr():
    os.environ["ENABLE_REMOTE_GPU_WORKFLOWS"] = "true"
    os.environ["REMOTE_GPU_MODE"] = "runpod"
    os.environ.setdefault("REMOTE_GPU_TIMEOUT_S", "1800")
    os.environ["WAN_ENABLE_4STEP_LORA"] = "false"
    from agents.mcp.model_api_flows import apply_production_flow, resolve_production_flow
    from core.remote_gpu_manager import get_manager, reset_manager

    apply_production_flow(
        resolve_production_flow(preset_name="remote_gpu_serverless"),
        explicit=True,
    )
    reset_manager()
    return get_manager()


def dump_graphs(mgr, still: Path) -> dict:
    """Build prompts without running GPU. Needs the still only for LoadImage name."""
    from core.remote_gpu_manager import patch_workflow

    os.environ["WAN_ENABLE_4STEP_LORA"] = "false"
    wf_off = mgr.prepare_video_workflow(
        still, prompt=PROMPT, seed=42, duration_s=5.0, width=512, height=896,
    )
    os.environ["WAN_ENABLE_4STEP_LORA"] = "true"
    wf_on = mgr.prepare_video_workflow(
        still, prompt=PROMPT, seed=42, duration_s=5.0, width=512, height=896,
    )
    os.environ["WAN_ENABLE_4STEP_LORA"] = "false"
    wf_hard = mgr.prepare_video_workflow(
        still,
        prompt=PROMPT,
        seed=42,
        duration_s=5.0,
        width=512,
        height=896,
        extra_patches=HARDCODE_4STEP_PATCHES,
    )
    report = {
        "finding": (
            "KSamplerAdvanced.steps in the SWITCH-ON payload is a LINK to "
            "ComfySwitchNode, never the integer 4. The sampler cannot 'receive' "
            "4 unless that switch evaluates true AND lazy-picks PrimitiveInt 118. "
            "Hardcode patch replaces steps/cfg/split with literals and rewires "
            "ModelSampling to LoraLoaderModelOnly, so ComfySwitchNode is not on "
            "the executed model/steps path."
        ),
        "switch_off_20step": _sampler_snapshot(wf_off),
        "switch_on_4step_flag": _sampler_snapshot(wf_on),
        "hardcoded_4step_bypass_switch": _sampler_snapshot(wf_hard),
        "note_runpod_logs": (
            "This worker's COMPLETED/FAILED status has no Comfy execution log, "
            "so we cannot print the in-node steps value at GPU runtime. Payload "
            "inspection is the available evidence for items 1-2."
        ),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "sampler_payload_dump.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )
    print(json.dumps(report, indent=2, default=str))
    return report


def run_hardcoded(mgr, still: Path) -> dict:
    import time

    OUT.mkdir(parents=True, exist_ok=True)
    dest = OUT / "hardcode_4step_5s.mp4"
    os.environ["WAN_ENABLE_4STEP_LORA"] = "false"
    t0 = time.monotonic()
    path = mgr.generate_video(
        still,
        prompt=PROMPT,
        output_path=dest,
        duration_s=5.0,
        width=512,
        height=896,
        seed=42,
        stem="hardcode_4step_5s",
        extra_patches=HARDCODE_4STEP_PATCHES,
    )
    wall = time.monotonic() - t0
    meta = dict(getattr(mgr.client, "last_job_meta", {}) or {})
    exec_ms = meta.get("executionTime")
    exec_s = (float(exec_ms) / 1000.0) if exec_ms is not None else float(
        getattr(mgr.client, "last_job_seconds", wall) or wall
    )
    delay_ms = meta.get("delayTime")
    report = {
        "label": "hardcode_4step_5s",
        "path": str(path),
        "bytes": Path(path).stat().st_size if Path(path).is_file() else 0,
        "wall_s": round(wall, 2),
        "execution_s": round(exec_s, 2),
        "queue_delay_s": round(float(delay_ms) / 1000.0, 2) if delay_ms is not None else None,
        "cost_usd_est": round(exec_s * USD_PER_S, 4),
        "job_id": meta.get("job_id"),
        "workerId": meta.get("workerId"),
        "vram_staging_present": meta.get("vram_staging_present"),
        "vram_staging_hits": meta.get("vram_staging_hits"),
        "log_excerpt_tail": str(meta.get("log_excerpt") or "")[-1500:],
        "patches": HARDCODE_4STEP_PATCHES,
    }
    (OUT / "report.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )
    print(json.dumps(report, indent=2, default=str))
    return report


def main() -> int:
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--dump-only", action="store_true")
    p.add_argument("--run", action="store_true")
    p.add_argument("--still", type=Path, default=STILL)
    args = p.parse_args()
    still = args.still
    if not still.is_file():
        print(f"ERROR still missing: {still}", file=sys.stderr)
        return 2
    mgr = _prep_mgr()
    dump_graphs(mgr, still)
    if args.dump_only and not args.run:
        return 0
    if args.run or not args.dump_only:
        run_hardcoded(mgr, still)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
