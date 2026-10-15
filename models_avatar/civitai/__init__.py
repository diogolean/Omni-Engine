# -*- coding: utf-8 -*-
"""Civitai download client and LoRA-training scaffold."""
from __future__ import annotations

import re

API_ROOT = "https://civitai.com/api/v1"
API_DOWNLOAD = "https://civitai.com/api/download/models"

_TYPE_DIRS = {
    "checkpoint": "checkpoints",
    "lora": "loras",
    "locon": "loras",
    "dora": "loras",
    "lycoris": "loras",
    "vae": "vae",
    "controlnet": "controlnet",
    "textualinversion": "embeddings",
    "hypernetwork": "hypernetworks",
    "upscaler": "upscale_models",
    "motionmodule": "animatediff_models",
    "poses": "poses",
}


def comfy_subdir(version_payload: dict) -> str:
    """Map a Civitai model type onto a ComfyUI ``models/`` subdirectory."""
    model = version_payload.get("model") if isinstance(version_payload, dict) else None
    raw = ""
    if isinstance(model, dict):
        raw = str(model.get("type") or "")
    if not raw and isinstance(version_payload, dict):
        raw = str(version_payload.get("type") or "")
    key = re.sub(r"[^a-z]", "", raw.lower())
    return _TYPE_DIRS.get(key, "other")


def safe_filename(name: str) -> str:
    from pathlib import Path

    from models_avatar.errors import CivitaiError

    base = Path(str(name).replace("\\", "/")).name
    if not base or base in {".", ".."} or "/" in base:
        raise CivitaiError(f"Unsafe Civitai filename: {name!r}")
    return base
