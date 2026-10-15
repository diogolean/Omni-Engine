# -*- coding: utf-8 -*-
"""RunPod serverless ComfyUI path.

The request shape matches the factory worker in ``core.remote_gpu_manager``:
``POST /run`` with ``input.workflow`` set to the API prompt. The owner still
needs a Docker image that contains the checkpoint, LoRAs, Impact Pack, and
ultralytics detectors. This module does not pretend that image already
exists. :func:`image_requirements` lists what the image has to ship.
"""
from __future__ import annotations

import base64
import logging
from typing import Any, Callable

from models_avatar.avatar_config import AvatarConfig
from models_avatar.backends.base import GenerationJob, GenerationResult
from models_avatar.env import (
    generation_timeout_s,
    poll_interval_s as default_poll_interval_s,
    runpod_api_key,
    runpod_endpoint_id,
)
from models_avatar.errors import GenerationError, RetryableGenerationError
from models_avatar.http_client import Transport, join_url, request, request_json

logger = logging.getLogger(__name__)

SleepFn = Callable[[float], None]
ClockFn = Callable[[], float]

_CUSTOM_NODES = (
    "ComfyUI-Impact-Pack (FaceDetailer)",
    "UltralyticsDetectorProvider from the Impact Pack / ultralytics install",
)
_PYTHON_PACKAGES = ("ultralytics",)


class RunPodServerlessBackend:
    name = "runpod_serverless"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        endpoint_id: str | None = None,
        poll_interval_s: float | None = None,
        timeout_s: float | None = None,
        transport: Transport | None = None,
        sleep: SleepFn | None = None,
        monotonic: ClockFn | None = None,
    ) -> None:
        import time

        self.api_key = runpod_api_key() if api_key is None else api_key
        self.endpoint_id = runpod_endpoint_id() if endpoint_id is None else endpoint_id
        self.base_url = (base_url if base_url is not None else _serverless_base(self.endpoint_id)).rstrip("/")
        self.poll_interval_s = (
            default_poll_interval_s() if poll_interval_s is None else poll_interval_s
        )
        self.timeout_s = timeout_s if timeout_s is not None else generation_timeout_s()
        self.transport = transport
        self.sleep = sleep or time.sleep
        self.monotonic = monotonic or time.monotonic

    def generate(self, job: GenerationJob) -> GenerationResult:
        if not self.base_url:
            raise GenerationError(
                "RunPod serverless needs RUNPOD_ENDPOINT_ID or RUNPOD_ENDPOINT_URL."
            )
        if not self.api_key:
            raise GenerationError("RunPod serverless needs RUNPOD_API_KEY.")
        job_id = self.queue(job.workflow)
        result = self.wait(job_id)
        payload = extract_image_bytes(result, job.save_node_id, transport=self.transport)
        job.dest.parent.mkdir(parents=True, exist_ok=True)
        job.dest.write_bytes(payload)
        logger.info(
            "models_avatar saved | backend=%s avatar=%s preset=%s seed=%s file=%s",
            self.name,
            job.avatar_id,
            job.preset,
            job.seed,
            job.dest,
        )
        return GenerationResult(
            path=job.dest,
            job_id=job_id,
            backend=self.name,
            seed=job.seed,
            preset=job.preset,
            prompt=job.positive,
            negative=job.negative,
        )

    def queue(self, workflow: dict[str, Any]) -> str:
        payload = build_run_payload(workflow, timeout_s=self.timeout_s)
        data = request_json(
            "POST",
            join_url(self.base_url, "run"),
            payload=payload,
            headers=self._headers(),
            timeout=self.timeout_s,
            transport=self.transport,
        )
        if not isinstance(data, dict):
            raise GenerationError(f"Unexpected RunPod /run response: {data!r}")
        job_id = data.get("id") or data.get("job_id")
        if not job_id:
            raise GenerationError(f"RunPod response missing job id: {data!r}")
        logger.info("models_avatar queued | backend=%s job_id=%s", self.name, job_id)
        return str(job_id)

    def wait(self, job_id: str) -> dict[str, Any]:
        deadline = self.monotonic() + self.timeout_s
        url = join_url(self.base_url, f"status/{job_id}")
        while self.monotonic() < deadline:
            data = request_json(
                "GET",
                url,
                headers=self._headers(),
                timeout=min(60.0, self.timeout_s),
                transport=self.transport,
            )
            if not isinstance(data, dict):
                self.sleep(self.poll_interval_s)
                continue
            status = str(data.get("status") or "").upper()
            if status in {"COMPLETED", "SUCCESS"}:
                logger.info(
                    "models_avatar completed | backend=%s job_id=%s",
                    self.name,
                    job_id,
                )
                return data
            if status == "TIMED_OUT":
                raise RetryableGenerationError(f"RunPod job {job_id} timed out: {data}")
            if status in {"FAILED", "CANCELLED", "ERROR"}:
                raise GenerationError(f"RunPod job {job_id} ended with status={status}: {data}")
            self.sleep(self.poll_interval_s)
        raise RetryableGenerationError(
            f"Timed out after {self.timeout_s:.0f}s waiting for RunPod job_id={job_id}"
        )

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }


def build_run_payload(workflow: dict[str, Any], *, timeout_s: float) -> dict[str, Any]:
    """Body for ``POST /v2/{endpoint_id}/run``.

    ``input.workflow`` is the ComfyUI API prompt (the same object posted to
    a pod's ``/prompt``). Text-to-image does not send ``images[]``.
    """
    seconds = max(int(timeout_s), 1)
    return {
        "input": {
            "workflow": workflow,
            "timeout": seconds,
            "workflow_timeout": seconds,
        },
        "policy": {"executionTimeout": seconds * 1000},
    }


def image_requirements(avatar: AvatarConfig) -> dict[str, Any]:
    """What the serverless Docker image needs for this avatar.

    ``fast`` only needs the checkpoint and LoRAs. ``ultra_detail`` also needs
    Impact Pack and the ultralytics bbox weights named by the preset.
    """
    detectors = sorted(
        {
            spec.detector
            for preset in avatar.presets.values()
            for spec in (preset.face_detailer, preset.hand_detailer)
            if spec.enabled and spec.detector
        }
    )
    return {
        "comfyui": "ComfyUI serving the API-format workflow used by /prompt.",
        "custom_nodes": list(_CUSTOM_NODES),
        "python_packages": list(_PYTHON_PACKAGES),
        "checkpoints": [avatar.checkpoint.filename],
        "loras": [lora.filename for lora in avatar.all_loras()],
        "ultralytics_bbox": detectors,
        "checkpoint_dir": "models/checkpoints",
        "lora_dir": "models/loras",
        "detector_dir": "models/ultralytics/bbox",
        "worker_input": "POST /run  {input: {workflow: <API prompt>}}",
        "worker_output": (
            "output.images[] or output.outputs[node].images[] with base64 "
            "in data, image, or data_base64, or an https url."
        ),
        "notes": [
            "The fast preset does not execute FaceDetailer.",
            "ultra_detail does not connect a SAM loader; the pod script did not either.",
            "If a future Impact Pack build requires SAM, add it under models/sams "
            "and wire sam_model_opt without changing the fast graph.",
            "Do not bake API tokens or .safetensors into the git repo. "
            "Download weights with the Civitai client onto the image or volume.",
        ],
    }


def extract_image_bytes(
    result: dict[str, Any],
    save_node_id: str,
    *,
    transport: Transport | None = None,
) -> bytes:
    files = collect_output_files(result)
    chosen = next(
        (item for item in files if str(item.get("node_id")) == str(save_node_id)),
        None,
    )
    if chosen is None and files:
        chosen = files[-1]
    if chosen is None:
        raise GenerationError(
            "Serverless result had no image payload. The worker must return "
            "base64 image bytes or a URL. See image_requirements()."
        )
    raw = (
        chosen.get("data_base64")
        or chosen.get("data")
        or chosen.get("image")
        or chosen.get("base64")
    )
    if isinstance(raw, str) and raw.strip():
        payload = raw.split(",", 1)[-1].strip() if raw.startswith("data:") else raw.strip()
        try:
            decoded = base64.b64decode(payload, validate=True)
        except Exception:  # noqa: BLE001
            decoded = b""
        if decoded:
            return decoded
    url = chosen.get("url") or chosen.get("file_url")
    if isinstance(url, str) and url.startswith("http"):
        response = request("GET", url, timeout=120.0, transport=transport)
        if not response.body:
            raise RetryableGenerationError(f"Empty serverless image url: {url}")
        return response.body
    raise GenerationError(
        "Serverless output has no base64 image or url. Update the Docker image "
        "so the worker returns image bytes. See image_requirements()."
    )


def collect_output_files(result: dict[str, Any]) -> list[dict[str, Any]]:
    output = result.get("output") if isinstance(result, dict) else None
    files: list[dict[str, Any]] = []
    if isinstance(output, dict):
        nested = output.get("outputs")
        if isinstance(nested, dict):
            for node_id, node_out in nested.items():
                _collect_node(files, node_out, node_id=str(node_id))
        elif isinstance(nested, list):
            for item in nested:
                if isinstance(item, dict):
                    files.append(item)
        for key in ("images", "files"):
            _collect_node(files, {key: output.get(key)}, node_id="")
    elif isinstance(output, list):
        for item in output:
            if isinstance(item, dict):
                files.append(item)
    return files


def _collect_node(files: list[dict[str, Any]], node_out: Any, *, node_id: str) -> None:
    if not isinstance(node_out, dict):
        return
    for key in ("images", "files"):
        items = node_out.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict):
                entry = dict(item)
                if node_id:
                    entry.setdefault("node_id", node_id)
                files.append(entry)


def _serverless_base(endpoint_id: str) -> str:
    import os

    explicit = (os.getenv("RUNPOD_ENDPOINT_URL") or "").split("#", 1)[0].strip().rstrip("/")
    if explicit:
        for suffix in ("/runsync", "/run", "/status"):
            if explicit.endswith(suffix):
                explicit = explicit[: -len(suffix)]
        return explicit
    if endpoint_id:
        return f"https://api.runpod.ai/v2/{endpoint_id}"
    return ""
