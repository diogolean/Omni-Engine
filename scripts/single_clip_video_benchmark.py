# -*- coding: utf-8 -*-
"""
Isolated single-clip video benchmarks (Phase 1).

One 7s clip, same still + prompt, no full reel. Does not publish.

Examples
--------
    python scripts/single_clip_video_benchmark.py --probe
    python scripts/single_clip_video_benchmark.py --config A
    python scripts/single_clip_video_benchmark.py --config C24
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
import re
import sys
import time
from pathlib import Path
from typing import Any, Mapping
from urllib.request import Request, urlopen

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
logger = logging.getLogger("single_clip_bench")

OUT_DIR = page_outputs_dir("ancient_knowledge") / "video_clip_benchmarks"
STILL_CANDIDATES = [
    ROOT / "outputs/ancient_knowledge/assets/ep_20260815_1830/work",
    ROOT / "outputs/ancient_knowledge/wan_reel_tests",
    ROOT / "outputs/ancient_knowledge/assets",
]

SHARED_PROMPT = (
    "Cinematic documentary live-action motion, mysterious ancient-world tone. "
    "This is IMAGE-TO-VIDEO: the still must come alive, not remain a freeze-frame. "
    "CAMERA: slow dolly-in toward the subject, foreground dust and stone sliding "
    "past the lens for true parallax depth. Moderate cinematic speed only — no whip "
    "pans, no jitter, no jump cuts. Dust motes drift; torchlight breathes. Smooth "
    "temporal coherence, no text overlays, no captions, no watermarks. Clip length "
    "about 7.0 seconds of continuous motion."
)

SERVERLESS_4090_USD_PER_S = 0.00031
# RunPod serverless 5090 / 32GB pool — public table ~$0.00049/s; confirm in console.
SERVERLESS_5090_USD_PER_S = 0.00049


def _api_key() -> str:
    return (os.getenv("RUNPOD_API_KEY") or "").strip()


def _endpoint_id() -> str:
    return (os.getenv("RUNPOD_ENDPOINT_ID") or "").strip()


def fetch_endpoint() -> dict[str, Any]:
    eid = _endpoint_id()
    req = Request(
        f"https://rest.runpod.io/v1/endpoints/{eid}",
        headers={"Authorization": f"Bearer {_api_key()}"},
    )
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _safe_endpoint_view(meta: Mapping[str, Any] | dict) -> dict[str, Any]:
    keys = (
        "id", "name", "gpuIds", "gpuCount", "workersMax", "workersMin",
        "idleTimeout", "networkVolumeId", "networkVolumeIds", "templateId",
        "scalerType", "scalerValue", "locations", "gpuTypeIds", "gpus",
        "allowedCudaVersions", "executionTimeoutMs",
    )
    out = {k: meta.get(k) for k in keys if k in meta}
    # Newer REST shape
    for k, v in meta.items():
        if k.lower() in {
            "gpuid", "gpuids", "gputype", "gputypes", "computetype",
            "workers", "workersstandby", "flashboot",
        }:
            out[k] = v
    return out


def list_volume_model_keys(limit: int = 400) -> list[str]:
    from core.remote_gpu_manager import _get_runpod_s3_client, _resolve_runpod_s3_creds

    creds = _resolve_runpod_s3_creds()
    if not creds:
        return []
    client = _get_runpod_s3_client(creds)
    keys: list[str] = []
    token = None
    prefixes = (
        "models/",
        "ComfyUI/models/",
        "comfyui/models/",
        "diffusion_models/",
        "loras/",
        "checkpoints/",
    )
    seen_pfx = set()
    for pfx in prefixes:
        token = None
        while True:
            kwargs: dict[str, Any] = {
                "Bucket": creds["volume_id"],
                "Prefix": pfx,
                "MaxKeys": 200,
            }
            if token:
                kwargs["ContinuationToken"] = token
            try:
                resp = client.list_objects_v2(**kwargs)
            except Exception as exc:  # noqa: BLE001
                logger.warning("S3 list failed prefix=%s: %s", pfx, exc)
                break
            for obj in resp.get("Contents") or []:
                key = str(obj.get("Key") or "")
                low = key.lower()
                if any(
                    tok in low
                    for tok in (
                        "wan", "ltx", "hunyuan", "hyvideo", "lightx2v",
                        "flux", "i2v", "t2v",
                    )
                ):
                    keys.append(f"{key} ({obj.get('Size', 0)} bytes)")
            token = resp.get("NextContinuationToken")
            if not token or len(keys) >= limit:
                break
        seen_pfx.add(pfx)
        if len(keys) >= limit:
            break
    return keys[:limit]


def find_still() -> Path:
    env_still = (os.getenv("BENCH_STILL") or "").strip()
    if env_still:
        p = Path(env_still)
        if p.is_file():
            return p
    patterns = ("*scene_01*.png", "*wan_01*.png", "*_v01_*.png", "*still*.png", "*.png")
    for folder in STILL_CANDIDATES:
        if not folder.exists():
            continue
        for pat in patterns:
            hits = sorted(folder.rglob(pat), key=lambda x: x.stat().st_mtime, reverse=True)
            for h in hits:
                if h.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"} and h.stat().st_size > 20_000:
                    return h
    raise FileNotFoundError("No benchmark still found under outputs/ancient_knowledge")


def _seconds_per_it(log_excerpt: str, steps: int) -> float | None:
    # ComfyUI KSampler: "12.34s/it" or "4.56 it/s"
    m = re.findall(r"([0-9]+(?:\.[0-9]+)?)\s*s/it", log_excerpt or "")
    if m:
        return float(m[-1])
    m = re.findall(r"([0-9]+(?:\.[0-9]+)?)\s*it/s", log_excerpt or "")
    if m:
        it_s = float(m[-1])
        return (1.0 / it_s) if it_s > 0 else None
    return None


def probe() -> dict[str, Any]:
    meta = fetch_endpoint()
    view = _safe_endpoint_view(meta)
    models = list_volume_model_keys()
    still = None
    try:
        still = str(find_still())
    except FileNotFoundError as exc:
        still = str(exc)
    lora_hits = [k for k in models if "lightx2v" in k.lower() or "4step" in k.lower()]
    ltx_hits = [k for k in models if "ltx" in k.lower()]
    hy_hits = [k for k in models if "hunyuan" in k.lower() or "hyvideo" in k.lower()]
    report = {
        "endpoint": view,
        "still": still,
        "lightx2v_lora_files": lora_hits,
        "ltx_files": ltx_hits,
        "hunyuan_files": hy_hits,
        "model_hits_n": len(models),
        "model_hits_sample": models[:80],
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "probe.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )
    print(json.dumps(report, indent=2, default=str))
    return report


def run_wan_config(
    *,
    label: str,
    still: Path,
    four_step: bool,
    steps: int | None,
    duration_s: float = 7.0,
    width: int = 512,
    height: int = 896,
    seed: int = 42,
    usd_per_s: float = SERVERLESS_4090_USD_PER_S,
    hardcode_4step_bypass_switch: bool = False,
) -> dict[str, Any]:
    os.environ["ENABLE_REMOTE_GPU_WORKFLOWS"] = "true"
    os.environ["REMOTE_GPU_MODE"] = "runpod"
    os.environ.setdefault("REMOTE_GPU_TIMEOUT_S", "1800")

    from agents.mcp.model_api_flows import apply_production_flow, resolve_production_flow
    from core.remote_gpu_manager import get_manager, reset_manager

    flow = resolve_production_flow(preset_name="remote_gpu_serverless")
    apply_production_flow(flow, explicit=True)
    # CRITICAL: set AFTER apply_production_flow. config.py load_dotenv(override=True)
    # runs on first `import config` inside apply and clobbers env back to .env.
    os.environ["WAN_ENABLE_4STEP_LORA"] = (
        "true" if (four_step or hardcode_4step_bypass_switch) else "false"
    )
    reset_manager()
    mgr = get_manager()

    out_dir = OUT_DIR / label
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"{label}.mp4"

    t0 = time.monotonic()
    path: Path | None = None
    err: str | None = None
    try:
        path = mgr.generate_video(
            still,
            prompt=SHARED_PROMPT,
            output_path=dest,
            duration_s=duration_s,
            width=width,
            height=height,
            seed=seed,
            steps=steps,
            stem=label,
            four_step=True if hardcode_4step_bypass_switch else four_step,
            hardcode_4step_bypass_switch=hardcode_4step_bypass_switch,
        )
    except Exception as exc:  # noqa: BLE001
        err = str(exc)
        logger.exception("generate_video failed: %s", exc)
    wall_s = time.monotonic() - t0
    meta = dict(getattr(mgr.client, "last_job_meta", {}) or {})
    gpu_s = float(getattr(mgr.client, "last_job_seconds", 0) or wall_s)
    exec_ms = meta.get("executionTime")
    exec_s = (float(exec_ms) / 1000.0) if exec_ms is not None else gpu_s
    delay_ms = meta.get("delayTime")
    delay_s = (float(delay_ms) / 1000.0) if delay_ms is not None else None
    logs = str(meta.get("log_excerpt") or "")
    sit = _seconds_per_it(logs, int(steps or (4 if four_step or hardcode_4step_bypass_switch else 20)))
    cost = exec_s * usd_per_s
    diag = dict(getattr(mgr, "last_video_workflow_diag", {}) or {})
    report = {
        "label": label,
        "status": "FAILED" if err else "OK",
        "error": err,
        "path": str(path) if path else None,
        "bytes": Path(path).stat().st_size if path and Path(path).is_file() else 0,
        "wall_s": round(wall_s, 2),
        "gpu_job_s": round(gpu_s, 2),
        "execution_s": round(exec_s, 2),
        "queue_delay_s": round(delay_s, 2) if delay_s is not None else None,
        "seconds_per_it": round(sit, 3) if sit is not None else None,
        "cost_usd_est": round(cost, 4),
        "usd_per_s_used": usd_per_s,
        "four_step_lora": four_step or hardcode_4step_bypass_switch,
        "hardcode_4step_bypass_switch": hardcode_4step_bypass_switch,
        "steps_override": steps,
        "duration_s": duration_s,
        "size": [width, height],
        "seed": seed,
        "workflow_diag": diag,
        "vram_staging_present": bool(meta.get("vram_staging_present")),
        "vram_staging_hits": meta.get("vram_staging_hits") or [],
        "workerId": meta.get("workerId"),
        "job_id": meta.get("job_id"),
        "log_excerpt_tail": logs[-1500:],
    }
    (out_dir / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return report


def dump_sampler_payloads() -> dict[str, Any]:
    """Dump what KSampler/Switch nodes actually contain after prepare, no GPU job."""
    from core.remote_gpu_manager import RemoteGPUManager

    mgr = RemoteGPUManager()
    dummy = page_outputs_dir("ancient_knowledge") / "video_clip_benchmarks" / "_dummy.png"
    dummy.parent.mkdir(parents=True, exist_ok=True)
    variants = {
        "switch_off_20step": {"four_step": False, "hardcode_4step_bypass_switch": False},
        "switch_on_4step": {"four_step": True, "hardcode_4step_bypass_switch": False},
        "hardcode_4step_bypass_switch": {
            "four_step": False,
            "hardcode_4step_bypass_switch": True,
        },
    }
    out: dict[str, Any] = {}
    for name, kwargs in variants.items():
        wf = mgr.prepare_video_workflow(
            dummy,
            prompt=SHARED_PROMPT,
            seed=42,
            width=512,
            height=896,
            duration_s=5.0,
            image_name="benchmark_dump_placeholder.png",
            **kwargs,
        )
        diag = dict(mgr.last_video_workflow_diag or {})
        slim = {
            "diag": diag,
            "ksampler": {},
            "lora": {},
            "primitive_bool_131": (wf.get("129:131") or {}).get("inputs"),
            "steps_prim_4": (wf.get("129:118") or {}).get("inputs"),
            "steps_prim_20": (wf.get("129:128") or {}).get("inputs"),
        }
        for nid in ("129:86", "129:85"):
            slim["ksampler"][nid] = (wf.get(nid) or {}).get("inputs")
        for nid in ("129:101", "129:102"):
            slim["lora"][nid] = (wf.get(nid) or {}).get("inputs")
        out[name] = slim
        dump_path = dummy.parent / f"sampler_payload_{name}.json"
        dump_path.write_text(json.dumps(slim, indent=2, default=str), encoding="utf-8")
        logger.info("Wrote %s", dump_path)
    summary_path = dummy.parent / "sampler_payload_dump.json"
    summary_path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(json.dumps(out, indent=2, default=str))
    return out


def main() -> int:
    p = argparse.ArgumentParser(description="Isolated 7s video clip benchmarks")
    p.add_argument("--probe", action="store_true")
    p.add_argument(
        "--config",
        choices=("A", "C24", "B", "C", "D", "E", "H", "HARD4"),
        default=None,
        help="A=24GB 10/20-step; C24=24GB 4-step LoRA via switch; "
        "HARD4=hardcode steps=4 bypass switch; B/C need 32GB GPU",
    )
    p.add_argument("--still", type=Path, default=None)
    p.add_argument("--duration", type=float, default=7.0, help="Clip length seconds")
    p.add_argument(
        "--dump-only",
        action="store_true",
        help="Prepare workflows (20-step / switch-4 / hardcode-4) and dump sampler diags. No GPU job.",
    )
    args = p.parse_args()
    if args.dump_only:
        dump_sampler_payloads()
        return 0
    if args.probe or args.config is None:
        probe()
        if args.config is None:
            return 0

    still = Path(args.still) if args.still else find_still()
    if not still.is_file():
        print(f"ERROR still missing: {still}", file=sys.stderr)
        return 2
    logger.info("Still: %s (%d bytes)", still, still.stat().st_size)

    cfg = args.config
    dur = float(args.duration)
    if cfg == "A":
        run_wan_config(
            label=f"A_wan22_24gb_10step_{dur:.0f}s",
            still=still, four_step=False, steps=None, duration_s=dur,
        )
        return 0
    if cfg == "C24":
        run_wan_config(
            label=f"C24_wan22_24gb_4step_lora_{dur:.0f}s",
            still=still, four_step=True, steps=None, duration_s=dur,
        )
        return 0
    if cfg == "HARD4":
        run_wan_config(
            label=f"HARD4_wan22_lightx2v_bypass_switch_{dur:.0f}s",
            still=still,
            four_step=True,
            steps=None,
            duration_s=dur,
            hardcode_4step_bypass_switch=True,
        )
        return 0
    if cfg == "B":
        run_wan_config(
            label="B_wan22_32gb_10step",
            still=still,
            four_step=False,
            steps=None,
            usd_per_s=SERVERLESS_5090_USD_PER_S,
        )
        return 0
    if cfg == "C":
        run_wan_config(
            label="C_wan22_32gb_4step_lora",
            still=still,
            four_step=True,
            steps=None,
            usd_per_s=SERVERLESS_5090_USD_PER_S,
        )
        return 0
    print(
        f"Config {cfg} is not wired as a live Wan job in this script "
        "(LTX/Hunyuan require models on the volume). Run --probe first.",
        file=sys.stderr,
    )
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
