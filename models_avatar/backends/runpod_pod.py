# -*- coding: utf-8 -*-
"""Live RunPod pod running the ComfyUI HTTP API.

This is the default backend. It talks to ``/prompt``, ``/history/{id}``, and
``/view`` on ``MODELS_AVATAR_POD_URL`` (or the factory ``REMOTE_GPU_BASE_URL``
/ ``COMFYUI_BASE_URL``). Requests use a browser User-Agent because the
Cloudflare proxy rejects Python's urllib agent.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Callable
from urllib.parse import urlencode

from models_avatar.backends.base import GenerationJob, GenerationResult
from models_avatar.env import (
    generation_timeout_s,
    pod_base_url,
    poll_interval_s as default_poll_interval_s,
    runpod_api_key,
)
from models_avatar.errors import GenerationError, RetryableGenerationError
from models_avatar.http_client import Transport, join_url, request, request_json

logger = logging.getLogger(__name__)

SleepFn = Callable[[float], None]
ClockFn = Callable[[], float]


class RunPodPodBackend:
    name = "runpod_pod"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        poll_interval_s: float | None = None,
        timeout_s: float | None = None,
        transport: Transport | None = None,
        sleep: SleepFn | None = None,
        monotonic: ClockFn | None = None,
    ) -> None:
        import time

        self.base_url = (base_url if base_url is not None else pod_base_url()).rstrip("/")
        self.api_key = runpod_api_key() if api_key is None else api_key
        self.poll_interval_s = (
            default_poll_interval_s() if poll_interval_s is None else poll_interval_s
        )
        self.timeout_s = timeout_s if timeout_s is not None else generation_timeout_s()
        self.transport = transport
        self.sleep = sleep or time.sleep
        self.monotonic = monotonic or time.monotonic
        self.client_id = str(uuid.uuid4())

    def generate(self, job: GenerationJob) -> GenerationResult:
        if not self.base_url:
            raise GenerationError(
                "RunPod pod URL is empty. Set MODELS_AVATAR_POD_URL, "
                "REMOTE_GPU_BASE_URL, or COMFYUI_BASE_URL."
            )
        prompt_id = self.queue_prompt(job.workflow)
        history = self.wait_for_history(prompt_id, job.save_node_id)
        image = _image_meta(history, job.save_node_id)
        payload = self.download_view(image)
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
            job_id=prompt_id,
            backend=self.name,
            seed=job.seed,
            preset=job.preset,
            prompt=job.positive,
            negative=job.negative,
        )

    def queue_prompt(self, workflow: dict[str, Any]) -> str:
        url = join_url(self.base_url, "prompt")
        data = request_json(
            "POST",
            url,
            payload={"prompt": workflow, "client_id": self.client_id},
            headers=self._headers(json_body=True),
            timeout=self.timeout_s,
            transport=self.transport,
        )
        if not isinstance(data, dict) or not data.get("prompt_id"):
            raise GenerationError(f"Unexpected /prompt response: {data!r}")
        node_errors = data.get("node_errors") or {}
        if node_errors:
            raise GenerationError(f"ComfyUI node_errors: {node_errors}")
        prompt_id = str(data["prompt_id"])
        logger.info("models_avatar queued | backend=%s prompt_id=%s", self.name, prompt_id)
        return prompt_id

    def wait_for_history(self, prompt_id: str, save_node_id: str) -> dict[str, Any]:
        deadline = self.monotonic() + self.timeout_s
        url = join_url(self.base_url, f"history/{prompt_id}")
        while self.monotonic() < deadline:
            hist = request_json(
                "GET",
                url,
                headers=self._headers(json_body=False),
                timeout=min(60.0, self.timeout_s),
                transport=self.transport,
            )
            entry = hist.get(prompt_id) if isinstance(hist, dict) else None
            if isinstance(entry, dict):
                status = entry.get("status") or {}
                if status.get("status_str") == "error":
                    raise GenerationError(
                        f"ComfyUI job failed: {status.get('messages') or entry}"
                    )
                outputs = entry.get("outputs") or {}
                if _node_has_image(outputs, save_node_id) or (
                    status.get("completed") is True and _any_image(outputs)
                ):
                    logger.info(
                        "models_avatar completed | backend=%s prompt_id=%s",
                        self.name,
                        prompt_id,
                    )
                    return entry
            self.sleep(self.poll_interval_s)
        raise RetryableGenerationError(
            f"Timed out after {self.timeout_s:.0f}s waiting for prompt_id={prompt_id}"
        )

    def download_view(self, image: dict[str, Any]) -> bytes:
        params = urlencode(
            {
                "filename": image.get("filename") or "",
                "subfolder": image.get("subfolder") or "",
                "type": image.get("type") or "output",
            }
        )
        url = join_url(self.base_url, "view?" + params)
        response = request(
            "GET",
            url,
            headers=self._headers(json_body=False),
            timeout=self.timeout_s,
            transport=self.transport,
        )
        if not response.body:
            raise RetryableGenerationError(f"Empty /view body for {image!r}")
        return response.body

    def _headers(self, *, json_body: bool) -> dict[str, str]:
        headers: dict[str, str] = {}
        if json_body:
            headers["Content-Type"] = "application/json"
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers


def _node_has_image(outputs: Any, node_id: str) -> bool:
    if not isinstance(outputs, dict):
        return False
    node = outputs.get(node_id)
    if not isinstance(node, dict):
        return False
    images = node.get("images")
    return isinstance(images, list) and bool(images)


def _any_image(outputs: Any) -> bool:
    if not isinstance(outputs, dict):
        return False
    return any(_node_has_image(outputs, node_id) for node_id in outputs)


def _image_meta(entry: dict[str, Any], save_node_id: str) -> dict[str, Any]:
    outputs = entry.get("outputs") or {}
    if not isinstance(outputs, dict):
        raise GenerationError(f"ComfyUI history has no outputs: {entry!r}")
    node = outputs.get(save_node_id)
    images = node.get("images") if isinstance(node, dict) else None
    if not images:
        for candidate in outputs.values():
            if isinstance(candidate, dict) and candidate.get("images"):
                images = candidate["images"]
                break
    if not images:
        raise GenerationError(f"ComfyUI history has no images: {entry!r}")
    image = images[-1]
    if not isinstance(image, dict) or not image.get("filename"):
        raise GenerationError(f"ComfyUI image meta missing filename: {image!r}")
    return image
