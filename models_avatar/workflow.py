# -*- coding: utf-8 -*-
"""Build an API-format ComfyUI prompt from an avatar preset.

``fast`` patches the avatar's checked-in workflow (the Sophie export is the
single-pass graph). ``ultra_detail`` keeps that pass and appends an optional
FaceDetailer and hand detailer. The detailer model is the checkpoint plus
the identity LoRA only, at the preset weight (0.85 for Sophie), so the skin
and style LoRAs stay on the base pass. Face and hand share that one LoRA
node: the hand detailer reuses the face LoRA weight whenever the face
detailer is enabled.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from models_avatar.avatar_config import AvatarConfig, DetailerSpec, PresetSpec
from models_avatar.errors import AvatarConfigError
from models_avatar.prompts import compose_negative, enforce_positive
from models_avatar.safety import merge_negatives

_FACE_DETECTOR_NODE = "30"
_HAND_DETECTOR_NODE = "31"
_DETAILER_LORA_NODE = "40"
_FACE_DETAIL_NODE = "33"
_HAND_DETAIL_NODE = "35"

# Widget values copied from the pod script that completed on the live ComfyUI.
_DETAILER_WIDGETS: dict[str, Any] = {
    "guide_size": 512,
    "guide_size_for": True,
    "max_size": 1024,
    "feather": 5,
    "noise_mask": True,
    "force_inpaint": True,
    "bbox_threshold": 0.4,
    "bbox_dilation": 10,
    "bbox_crop_factor": 2.5,
    "sam_detection_hint": "center-1",
    "sam_dilation": 0,
    "sam_threshold": 0.93,
    "sam_bbox_expansion": 0,
    "sam_mask_hint_threshold": 0.7,
    "sam_mask_hint_use_negative": "False",
    "drop_size": 10,
    "wildcard": "",
    "cycle": 1,
    "inpaint_model": False,
    "noise_mask_feather": 20,
    "tiled_encode": False,
    "tiled_decode": False,
}

_WORKFLOW_CACHE: dict[str, tuple[int, dict[str, Any]]] = {}


def load_workflow_template(path: Path) -> dict[str, Any]:
    """Return a deep copy of an API workflow, cached by mtime."""
    stat = path.stat()
    key = str(path)
    stamp = stat.st_mtime_ns
    cached = _WORKFLOW_CACHE.get(key)
    if cached is None or cached[0] != stamp:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise AvatarConfigError(f"Workflow {path} must be a JSON object.")
        _WORKFLOW_CACHE[key] = (stamp, data)
        cached = _WORKFLOW_CACHE[key]
    return json.loads(json.dumps(cached[1]))


def build_workflow(
    avatar: AvatarConfig,
    preset: PresetSpec,
    *,
    positive: str,
    negative: str | None,
    seed: int,
    filename_prefix: str,
) -> dict[str, Any]:
    """Patch the avatar graph and append detailers when the preset enables them."""
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise AvatarConfigError(f"Seed must be an int, got {seed!r}.")
    enforce_positive(avatar, positive)
    merged_negative = merge_negatives(compose_negative(avatar), negative)

    workflow = load_workflow_template(avatar.workflow_path)
    nodes = avatar.graph.nodes
    _set_checkpoint(workflow, nodes["checkpoint"], avatar.checkpoint.filename)
    _set_lora(
        workflow,
        avatar.identity_lora.node or nodes["identity_lora"],
        avatar.identity_lora.filename,
        avatar.identity_lora.weight,
        avatar.identity_lora.clip,
    )
    for lora in avatar.extra_loras:
        assert lora.node is not None
        _set_lora(workflow, lora.node, lora.filename, lora.weight, lora.clip)

    workflow[nodes["positive"]]["inputs"]["text"] = positive
    workflow[nodes["negative"]]["inputs"]["text"] = merged_negative

    latent = workflow[nodes["latent"]]["inputs"]
    latent["width"] = preset.width
    latent["height"] = preset.height
    latent["batch_size"] = 1

    sampler = workflow[nodes["sampler"]]["inputs"]
    sampler["seed"] = seed
    sampler["steps"] = preset.steps
    sampler["cfg"] = preset.cfg
    sampler["sampler_name"] = preset.sampler_name
    sampler["scheduler"] = preset.scheduler
    sampler["denoise"] = preset.denoise

    save = workflow[nodes["save"]]["inputs"]
    save["filename_prefix"] = _safe_prefix(filename_prefix)

    if preset.face_detailer.enabled or preset.hand_detailer.enabled:
        _append_detailers(workflow, avatar, preset, seed=seed)
    return workflow


def save_node_id(avatar: AvatarConfig) -> str:
    return avatar.graph.nodes["save"]


def _append_detailers(
    workflow: dict[str, Any],
    avatar: AvatarConfig,
    preset: PresetSpec,
    *,
    seed: int,
) -> None:
    nodes = avatar.graph.nodes
    for node_id in (
        _FACE_DETECTOR_NODE,
        _HAND_DETECTOR_NODE,
        _DETAILER_LORA_NODE,
        _FACE_DETAIL_NODE,
        _HAND_DETAIL_NODE,
    ):
        if node_id in workflow:
            raise AvatarConfigError(
                f"Workflow already uses node {node_id}, which ultra_detail needs."
            )

    weight = _detailer_weight(preset)
    workflow[_DETAILER_LORA_NODE] = {
        "class_type": "LoraLoaderModelOnly",
        "inputs": {
            "model": [nodes["checkpoint"], 0],
            "lora_name": avatar.identity_lora.filename,
            "strength_model": weight,
        },
    }

    positive = workflow[nodes["positive"]]["inputs"]["clip"]
    vae = workflow[nodes["decode"]]["inputs"]["vae"]
    image: list[Any] = [nodes["decode"], 0]

    if preset.face_detailer.enabled:
        workflow[_FACE_DETECTOR_NODE] = _detector(preset.face_detailer.detector)
        workflow[_FACE_DETAIL_NODE] = _detailer_node(
            preset.face_detailer,
            image=image,
            detector_node=_FACE_DETECTOR_NODE,
            clip=positive,
            vae=vae,
            positive_node=nodes["positive"],
            negative_node=nodes["negative"],
            seed=seed,
        )
        image = [_FACE_DETAIL_NODE, 0]

    if preset.hand_detailer.enabled:
        workflow[_HAND_DETECTOR_NODE] = _detector(preset.hand_detailer.detector)
        workflow[_HAND_DETAIL_NODE] = _detailer_node(
            preset.hand_detailer,
            image=image,
            detector_node=_HAND_DETECTOR_NODE,
            clip=positive,
            vae=vae,
            positive_node=nodes["positive"],
            negative_node=nodes["negative"],
            seed=seed,
        )
        image = [_HAND_DETAIL_NODE, 0]

    workflow[nodes["save"]]["inputs"]["images"] = image


def _detailer_weight(preset: PresetSpec) -> float:
    """Weight for the one detailer LoRA node shared by face and hands.

    Node 40 is loaded once. The hand FaceDetailer reads that same model, so
    it reuses the face ``identity_lora_weight`` when the face detailer is
    enabled. ``hand_detailer.identity_lora_weight`` applies only when the
    face detailer is off.
    """
    if preset.face_detailer.enabled:
        return preset.face_detailer.identity_lora_weight
    return preset.hand_detailer.identity_lora_weight


def _detector(model_name: str) -> dict[str, Any]:
    return {
        "class_type": "UltralyticsDetectorProvider",
        "inputs": {"model_name": model_name},
    }


def _detailer_node(
    spec: DetailerSpec,
    *,
    image: list[Any],
    detector_node: str,
    clip: list[Any],
    vae: list[Any],
    positive_node: str,
    negative_node: str,
    seed: int,
) -> dict[str, Any]:
    inputs: dict[str, Any] = {
        "image": image,
        "model": [_DETAILER_LORA_NODE, 0],
        "clip": clip,
        "vae": vae,
        "seed": seed,
        "steps": spec.steps,
        "cfg": spec.cfg,
        "sampler_name": "dpmpp_2m",
        "scheduler": "karras",
        "positive": [positive_node, 0],
        "negative": [negative_node, 0],
        "denoise": spec.denoise,
        "bbox_detector": [detector_node, 0],
    }
    inputs.update(_DETAILER_WIDGETS)
    return {"class_type": "FaceDetailer", "inputs": inputs}


def _set_checkpoint(workflow: dict[str, Any], node_id: str, filename: str) -> None:
    node = _expect(workflow, node_id, "CheckpointLoaderSimple")
    node["inputs"]["ckpt_name"] = filename


def _set_lora(
    workflow: dict[str, Any],
    node_id: str,
    filename: str,
    weight: float,
    clip_weight: float,
) -> None:
    node = _expect(workflow, node_id, "LoraLoader")
    node["inputs"]["lora_name"] = filename
    node["inputs"]["strength_model"] = weight
    node["inputs"]["strength_clip"] = clip_weight


def _expect(workflow: dict[str, Any], node_id: str, class_type: str) -> dict[str, Any]:
    try:
        node = workflow[node_id]
    except KeyError as exc:
        raise AvatarConfigError(f"Workflow is missing node {node_id}.") from exc
    if node.get("class_type") != class_type:
        raise AvatarConfigError(
            f"Node {node_id} is {node.get('class_type')!r}, expected {class_type}."
        )
    return node


def _safe_prefix(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("._")
    return cleaned or "avatar"
