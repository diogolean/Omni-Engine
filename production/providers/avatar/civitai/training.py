# -*- coding: utf-8 -*-
"""LoRA training job scaffold.

The public Civitai REST API documents model discovery and file download.
It does not document an endpoint that creates a LoRA training job. On-site
training is a logged-in browser flow. This module builds the payload we
would send and stops there.
"""
from __future__ import annotations

from typing import Any

from production.providers.avatar.avatar_config import AvatarConfig
from production.providers.avatar.errors import BackendNotImplemented


def build_training_payload(
    avatar: AvatarConfig,
    *,
    dataset_dir: str,
    base_model: str = "sdxl",
    epochs: int = 10,
    resolution: int = 1024,
    network_dim: int = 32,
    network_alpha: int = 16,
) -> dict[str, Any]:
    """JSON body to submit once Civitai publishes a training route.

    ``dataset_dir`` is a path on the machine that will run training. This
    function does not upload images and does not read their bytes.
    """
    if not dataset_dir.strip():
        raise ValueError("dataset_dir is required.")
    return {
        "name": f"{avatar.identity.name} LoRA",
        "trigger_word": avatar.identity.trigger,
        "base_model": base_model,
        "dataset_dir": dataset_dir,
        "epochs": epochs,
        "resolution": resolution,
        "network_dim": network_dim,
        "network_alpha": network_alpha,
        "output_filename": avatar.identity_lora.filename,
    }


def submit_training_job(payload: dict[str, Any]) -> None:
    """Submit ``payload`` to Civitai.

    TODO(civitai): GET /api/v1/models, GET /api/v1/model-versions/{id}, and
    GET /api/download/models/{id} are the documented public routes. None of
    them creates a training job. Do not POST this payload to a guessed URL.
    When Civitai publishes a training endpoint, send ``payload`` there with
    the same Bearer token :class:`models_avatar.civitai.client.CivitaiClient`
    already uses, then return the job id.
    """
    if not payload.get("trigger_word") or not payload.get("dataset_dir"):
        raise ValueError("Training payload needs trigger_word and dataset_dir.")
    raise BackendNotImplemented(
        "Civitai's public API does not support creating a LoRA training job. "
        "build_training_payload() returns the body to send when a documented "
        "endpoint exists. See the TODO in submit_training_job."
    )
