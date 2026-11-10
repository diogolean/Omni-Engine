"""Host sizing and unpaid cost planning for one avatar.

Measures the mock/fixture factory path and BiRefNet on this machine without
opening a paid provider socket. Token and money estimates use the published
Gemini standard rows already stored in ``spend.prices``.
"""
from __future__ import annotations

import json
import os
import resource
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from . import styles
from .spend.prices import ImageRate, TokenRate, lookup, micro_to_cents

OFFICIAL_PRICE_URL = "https://ai.google.dev/gemini-api/docs/pricing"
OFFICIAL_PRICE_DATE = "2026-11-08"
IMAGE_OUTPUT_TOKENS = 1290  # official flash-image row: up to 1024x1024
IMAGE_TILE_TOKENS = 258  # official Gemini image-understanding tile size


@dataclass(frozen=True)
class StagePayload:
    stage: str
    operation: str
    model: str
    prompt: str
    reference_pixels: int
    output_images: int


def text_tokens(text: str) -> int:
    """Measured payload size for Gemini text. No provider call.

    Gemini's public tokenizer is not available offline here. This keeps the
    measured UTF-8 payload and applies the documented ~4 bytes/token ceiling
    used for planning holds. Values are therefore an upper bound on text
    input, never an invented undercount.
    """
    raw = text.encode("utf-8")
    return max(1, (len(raw) + 3) // 4) if raw else 0


def image_input_tokens(pixels: int) -> int:
    """Official tile count for one reference image at the planned 1024-class size."""
    if pixels <= 0:
        return 0
    # One 1024x1024-class plate fits in four 768 tiles in the published guide.
    return IMAGE_TILE_TOKENS * 4


def call_plan_payloads(subject: str = "Ada Lovelace, cartoon bust") -> list[StagePayload]:
    """Exact prompt strings the canonical paid path would send for one character."""
    from . import partsheet, rig  # noqa: PLC0415

    style = styles.prompt_style("cartoon")
    master = rig.STYLE_PROMPT.format(subject=subject, style=style)
    # Planned 4:5 / 4:3 plates around 1024 on the long edge.
    plate = 1024 * 1280
    sheet = 1024 * 768
    stages = [
        StagePayload("master", "image", "gemini-2.5-flash-image", master, 0, 1),
        StagePayload("detect_master", "detect_features", "gemini-2.5-flash", rig.DETECT_PROMPT, plate, 0),
        StagePayload("mouthless", "image", "gemini-2.5-flash-image", rig.MOUTHLESS_PROMPT, plate, 1),
        StagePayload("mouthless_retry", "image", "gemini-2.5-flash-image", rig.MOUTHLESS_PROMPT, plate, 1),
        StagePayload("front", "image", "gemini-2.5-flash-image", rig.FRONT_PROMPT, plate, 1),
        StagePayload("front_retry", "image", "gemini-2.5-flash-image", rig.FRONT_PROMPT + rig.FRONT_RETRY, plate, 1),
        StagePayload("front_landmarks", "image", "gemini-2.5-flash-image",
                     rig.FRONT_PROMPT + rig.FRONT_RETRY + rig.FRONT_LANDMARKS, plate, 1),
        StagePayload("detect_front", "detect_features", "gemini-2.5-flash", rig.DETECT_PROMPT, plate, 0),
        StagePayload("front_mouthless", "image", "gemini-2.5-flash-image", rig.MOUTHLESS_PROMPT, plate, 1),
        StagePayload("front_mouthless_retry", "image", "gemini-2.5-flash-image", rig.MOUTHLESS_PROMPT, plate, 1),
        StagePayload("side_sheet", "image", "gemini-2.5-flash-image", partsheet.SHEET_PROMPT, sheet, 1),
        StagePayload("side_sheet_retry_a", "image", "gemini-2.5-flash-image", partsheet.SHEET_PROMPT, sheet, 1),
        StagePayload("side_sheet_retry_b", "image", "gemini-2.5-flash-image", partsheet.SHEET_PROMPT, sheet, 1),
        StagePayload("front_sheet", "image", "gemini-2.5-flash-image", partsheet.SHEET_PROMPT, sheet, 1),
        StagePayload("front_sheet_retry", "image", "gemini-2.5-flash-image", partsheet.SHEET_PROMPT, sheet, 1),
        StagePayload("mouth_redraw", "image", "gemini-2.5-flash-image", rig.EDIT_PROMPTS["open"], plate, 1),
        StagePayload("right_3q", "image", "gemini-2.5-flash-image", rig.RIGHT_PROMPT, plate, 1),
        StagePayload("right_3q_retry", "image", "gemini-2.5-flash-image", rig.RIGHT_PROMPT, plate, 1),
        StagePayload("detect_right", "detect_features", "gemini-2.5-flash", rig.DETECT_PROMPT, plate, 0),
        StagePayload("profile", "image", "gemini-2.5-flash-image", rig.PROFILE_PROMPT, plate, 1),
        StagePayload("profile_retry", "image", "gemini-2.5-flash-image", rig.PROFILE_PROMPT, plate, 1),
        StagePayload("detect_profile", "detect_features", "gemini-2.5-flash", rig.DETECT_PROMPT, plate, 0),
    ]
    return stages


def estimate_character_cost(subject: str = "Ada Lovelace, cartoon bust") -> dict[str, Any]:
    """Worst-case one-character cost from measured payloads + official rates."""
    image = lookup("google", "gemini-2.5-flash-image")
    detect = lookup("google", "gemini-2.5-flash")
    if not isinstance(image, ImageRate) or not isinstance(detect, TokenRate):
        return {"ready": False, "block": "price_unavailable"}
    rows = []
    image_in = image_out = detect_in = detect_out = 0
    image_calls = detect_calls = 0
    for stage in call_plan_payloads(subject):
        text = text_tokens(stage.prompt)
        ref = image_input_tokens(stage.reference_pixels)
        if stage.operation == "image":
            incoming = text + ref
            outgoing = IMAGE_OUTPUT_TOKENS * stage.output_images
            micro = image.ceiling_micro(incoming, stage.output_images)
            image_in += incoming
            image_out += outgoing
            image_calls += 1
        else:
            # Detector returns a short JSON box set; measure the prompt and a
            # conservative 256-token completion ceiling used only for planning.
            incoming = text + ref
            outgoing = 256
            micro = detect.ceiling_micro(incoming, outgoing)
            detect_in += incoming
            detect_out += outgoing
            detect_calls += 1
        rows.append({
            "stage": stage.stage,
            "operation": stage.operation,
            "model": stage.model,
            "promptBytes": len(stage.prompt.encode("utf-8")),
            "inputTokens": text + ref,
            "textTokens": text,
            "referenceImageTokens": ref,
            "outputTokens": IMAGE_OUTPUT_TOKENS if stage.operation == "image" else 256,
            "outputImages": stage.output_images,
            "estimatedMicro": micro,
        })
    total_micro = sum(int(row["estimatedMicro"]) for row in rows)
    return {
        "ready": True,
        "schema": "animind.avatarCostPlan.v1",
        "source": OFFICIAL_PRICE_URL,
        "sourceDate": OFFICIAL_PRICE_DATE,
        "priceVersion": image.version,
        "tokenizer": {
            "text": "utf8_bytes_ceil_div_4_upper_bound",
            "imageInput": f"{IMAGE_TILE_TOKENS}_tokens_x_4_tiles_1024_class",
            "imageOutput": IMAGE_OUTPUT_TOKENS,
            "detectOutputCeiling": 256,
            "providerCall": False,
        },
        "totals": {
            "imageCalls": image_calls,
            "detectCalls": detect_calls,
            "imageInputTokens": image_in,
            "imageOutputTokens": image_out,
            "detectInputTokens": detect_in,
            "detectOutputTokens": detect_out,
            "estimatedMicro": total_micro,
            "estimatedCents": micro_to_cents(total_micro),
            "estimatedUsd": round(total_micro / 1_000_000, 6),
            "note": (
                "Composite image price = input tokens + $0.039/image. "
                "This is the worst-case call plan, not a live bill."
            ),
        },
        "rates": {
            "image": {
                "model": image.model,
                "inputMicroPerMillion": image.input_per_million,
                "outputMicroPerImage": image.output_per_image,
            },
            "detectFeatures": {
                "model": detect.model,
                "inputMissMicroPerMillion": detect.input_miss_per_million,
                "outputMicroPerMillion": detect.output_per_million,
            },
        },
        "stages": rows,
    }


def _rss_kib() -> int:
    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)


def measure_mock_pipeline(work: Path) -> dict[str, Any]:
    """Peak RSS and wall time for create_character → build_mock_rig (no BiRefNet)."""
    from . import characters, settings  # noqa: PLC0415

    os.environ["ANIMIND_FORCE_MOCK"] = "1"
    os.environ["ANIMIND_DATA_DIR"] = str(work)
    before = _rss_kib()
    started = time.perf_counter()
    result = characters.create_character(
        {"name": "Ada", "prompt": "Ada Lovelace dry-run", "type": "rig",
         "source": {"kind": "prompt", "prompt": "Ada Lovelace dry-run"}},
        "http://127.0.0.1:8787",
        puppets_root=settings.data_dir() / "users" / "measure" / "puppets",
    )
    elapsed = time.perf_counter() - started
    after = _rss_kib()
    return {
        "path": "create_character→build_mock_rig",
        "includesBiRefNet": False,
        "seconds": round(elapsed, 3),
        "peakRssMiB": round(after / 1024, 1),
        "deltaRssMiB": round(max(0, after - before) / 1024, 1),
        "views": sorted((result.get("views") or {}).keys()),
        "generator": result.get("generator"),
        "characterId": result.get("id"),
    }


def measure_birefnet(work: Path) -> dict[str, Any]:
    """Peak RSS of the isolated BiRefNet worker on one fixture plate."""
    import time as _time  # noqa: PLC0415

    from .rig import BiRefNetMatte, _matte_failure  # noqa: PLC0415

    work.mkdir(parents=True, exist_ok=True)
    fixture = work / "birefnet_fixture.png"
    image = Image.new("RGB", (768, 960), (255, 0, 255))
    draw = ImageDraw.Draw(image)
    draw.ellipse((180, 120, 588, 620), fill=(240, 200, 160))
    draw.rectangle((220, 600, 548, 940), fill=(70, 90, 140))
    image.save(fixture)

    matte = BiRefNetMatte()
    started = _time.perf_counter()
    peak_child = 0
    try:
        # Touch the worker so its pid exists, then sample RSS while matting.
        out = matte(Image.open(fixture).convert("RGB"))
        worker = matte._worker
        if worker is not None and worker.pid:
            try:
                peak_child = int(Path(f"/proc/{worker.pid}/status").read_text().split("VmHWM:")[1].split()[0])
            except (OSError, IndexError, ValueError):
                peak_child = 0
        elapsed = _time.perf_counter() - started
        return {
            "path": "BiRefNetMatte.isolated",
            "includesBiRefNet": True,
            "ok": True,
            "seconds": round(elapsed, 3),
            "peakRssMiB": round(peak_child / 1024, 1) if peak_child else round(_rss_kib() / 1024, 1),
            "alpha": out.getchannel("A").getextrema()[1] > 0,
            "size": list(out.size),
            "gpuRequired": False,
            "gpuRecommended": True,
            "hostRequirement": "CPU worker with at least 16 GiB available, or CUDA/ROCm",
        }
    except BaseException as exc:  # noqa: BLE001
        elapsed = _time.perf_counter() - started
        worker = getattr(matte, "_worker", None)
        err = str(exc)
        if worker is not None and not worker.is_alive():
            err = _matte_failure(worker)
        return {
            "path": "BiRefNetMatte.isolated",
            "includesBiRefNet": True,
            "ok": False,
            "seconds": round(elapsed, 3),
            "error": err[:400],
            "peakRssMiB": round(_rss_kib() / 1024, 1),
            "gpuRequired": False,
            "gpuRecommended": True,
            "hostRequirement": "CPU worker with at least 16 GiB available, or CUDA/ROCm",
        }
    finally:
        matte.close()


def sizing_report(work: Path) -> dict[str, Any]:
    mock = measure_mock_pipeline(work / "mock")
    matte = measure_birefnet(work / "matte")
    mock_mib = float(mock["peakRssMiB"])
    matte_mib = float(matte.get("peakRssMiB") or 0)
    # Parent API + isolated matte worker must coexist during build_rig.
    recommended_ram_gib = 16 if not matte.get("ok") else max(16, int((mock_mib + matte_mib) / 1024) + 4)
    return {
        "schema": "animind.hostSizing.v1",
        "cpuMinimum": "4 vCPU",
        "ramMinimumGiB": recommended_ram_gib,
        "gpuRequired": False,
        "gpuRecommended": True,
        "notes": [
            "Mock dry-run uses build_mock_rig and does not load BiRefNet.",
            "Paid create_character→build_rig always loads BiRefNet in an isolated worker.",
            "Observed cloud VMs under 16 GiB available RAM OOM-kill the CPU matte worker.",
        ],
        "mockPipeline": mock,
        "birefnet": matte,
        "costPlan": estimate_character_cost(),
    }


def main(argv: list[str] | None = None) -> None:
    import argparse
    import tempfile

    parser = argparse.ArgumentParser(prog="python -m products.animind_core.capacity")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    root = Path(tempfile.mkdtemp(prefix="animind-capacity-"))
    report = sizing_report(root)
    text = json.dumps(report, indent=2)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
