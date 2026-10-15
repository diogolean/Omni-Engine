# -*- coding: utf-8 -*-
"""Avatar config, safety negatives, prompts, and ComfyUI graphs."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from channel_loader import VALID_PAGES
from models_avatar.avatar_config import load_avatar
from models_avatar.channel_bridge import channel_descriptor
from models_avatar.errors import PromptPolicyError
from models_avatar.prompts import compose_positive, default_template_path, load_templates
from models_avatar.safety import MANDATORY_NEGATIVE_TERMS, merge_negatives
from models_avatar.workflow import build_workflow

_REPO = Path(__file__).resolve().parents[2]
_SCENE = (
    "relaxed closed-mouth smile, standing with shoulders square to the camera, "
    "waist-up portrait facing the camera, wearing a beige t-shirt and light blue jeans, "
    "in a quiet room, soft morning window light"
)
_REQUIRED_SAFETY = (
    "nude",
    "topless",
    "nipples",
    "exposed genitals",
    "explicit",
    "child",
    "teen",
    "underage",
    "see-through",
    "sheer",
    "transparent fabric",
    "visible nipples through clothing",
    "nipple outline",
    "wet t-shirt",
)


def _terms(text: str) -> set[str]:
    return {part.strip().casefold() for part in text.split(",") if part.strip()}


def test_sophie_is_not_a_video_channel() -> None:
    assert "sophie_bennet" not in VALID_PAGES
    descriptor = channel_descriptor("sophie_bennet")
    assert descriptor["registered_in_channel_loader"] is False
    assert descriptor["trigger"] == "Sophie Bennet"
    assert "fast" in descriptor["presets"]
    assert "ultra_detail" in descriptor["presets"]


def test_package_does_not_reference_aiwake() -> None:
    root = _REPO / "models_avatar"
    for path in root.rglob("*"):
        if path.suffix not in {".py", ".md", ".yaml", ".yml", ".json"}:
            continue
        assert "aiwake" not in path.read_text(encoding="utf-8").lower()


def test_sophie_fast_spec_matches_the_reference_pass() -> None:
    avatar = load_avatar("sophie_bennet")
    fast = avatar.preset("fast")
    assert avatar.checkpoint.filename == "cyberrealisticXL_v100_fp16.safetensors"
    assert avatar.identity_lora.filename == (
        "Sophie-bennet-lora-X2ZY233J64G7JVYMHAC0GWE9H0.safetensors"
    )
    assert avatar.identity_lora.weight == pytest.approx(0.75)
    weights = {lora.filename: lora.weight for lora in avatar.extra_loras}
    assert weights["perfection_style_SDXL.safetensors"] == pytest.approx(0.6)
    assert weights["skin_texture_XL_v4.safetensors"] == pytest.approx(0.10)
    assert weights["skin_realism_imperfections_xl.safetensors"] == pytest.approx(0.10)
    assert avatar.extra_loras[-1].civitai_version_id == 340833
    assert fast.sampler_name == "dpmpp_2m"
    assert fast.scheduler == "karras"
    assert fast.steps == 30
    assert fast.cfg == pytest.approx(5)
    assert (fast.width, fast.height) == (832, 1216)
    assert fast.face_detailer.enabled is False
    assert fast.hand_detailer.enabled is False
    ultra = avatar.preset("ultra_detail")
    assert ultra.face_detailer.enabled is True
    assert ultra.face_detailer.detector == "bbox/face_yolov8m.pt"
    assert ultra.face_detailer.denoise == pytest.approx(0.48)
    assert ultra.face_detailer.identity_lora_weight == pytest.approx(0.85)
    assert ultra.hand_detailer.enabled is True
    assert ultra.hand_detailer.detector == "bbox/hand_yolov8s.pt"
    assert ultra.hand_detailer.denoise == pytest.approx(0.38)


def test_reference_workflow_file_keeps_the_lora_chain() -> None:
    path = _REPO / "models_avatar" / "avatars" / "sophie_bennet" / "workflow_fast.json"
    workflow = json.loads(path.read_text(encoding="utf-8"))
    assert workflow["1"]["inputs"]["ckpt_name"] == "cyberrealisticXL_v100_fp16.safetensors"
    assert workflow["2"]["inputs"]["model"] == ["1", 0]
    assert workflow["3"]["inputs"]["model"] == ["2", 0]
    assert workflow["20"]["inputs"]["model"] == ["3", 0]
    assert workflow["25"]["inputs"]["model"] == ["20", 0]
    assert workflow["7"]["inputs"]["model"] == ["25", 0]
    assert workflow["7"]["inputs"]["sampler_name"] == "dpmpp_2m"
    assert workflow["7"]["inputs"]["scheduler"] == "karras"
    assert workflow["6"]["inputs"]["width"] == 832
    assert workflow["6"]["inputs"]["height"] == 1216


def test_transparency_negatives_are_mandatory() -> None:
    """A thin white tee rendered nipples. Those phrases stay in every negative."""
    merged = merge_negatives("cartoon")
    terms = _terms(merged)
    for phrase in (
        "see-through",
        "sheer",
        "transparent fabric",
        "visible nipples through clothing",
        "nipple outline",
        "wet t-shirt",
    ):
        assert phrase in terms
    avatar = load_avatar("sophie_bennet")
    workflow = build_workflow(
        avatar,
        avatar.preset("fast"),
        positive=compose_positive(avatar, _SCENE),
        negative="blurry",
        seed=3,
        filename_prefix="safety",
    )
    negative_terms = _terms(workflow["5"]["inputs"]["text"])
    assert "wet t-shirt" in negative_terms
    assert "visible nipples through clothing" in negative_terms


def test_mandatory_negatives_survive_an_empty_or_replacement_list() -> None:
    merged = merge_negatives("", "blurry")
    terms = _terms(merged)
    for term in _REQUIRED_SAFETY:
        assert term in terms
    for term in MANDATORY_NEGATIVE_TERMS:
        assert term.casefold() in terms
    assert "blurry" in terms


def test_builder_applies_safety_and_leaves_the_fast_graph_single_pass() -> None:
    avatar = load_avatar("sophie_bennet")
    positive = compose_positive(avatar, _SCENE)
    workflow = build_workflow(
        avatar,
        avatar.preset("fast"),
        positive=positive,
        negative="blurry",
        seed=7,
        filename_prefix="sophie_fast_7",
    )
    assert positive.startswith("Sophie Bennet,")
    assert ", unedited candid photo," in positive
    assert "blonde" not in workflow["4"]["inputs"]["text"].casefold()
    assert "freckles" not in workflow["4"]["inputs"]["text"].casefold()
    terms = _terms(workflow["5"]["inputs"]["text"])
    for term in _REQUIRED_SAFETY:
        assert term in terms
    assert workflow["7"]["inputs"]["seed"] == 7
    assert workflow["7"]["inputs"]["model"] == ["25", 0]
    assert workflow["9"]["inputs"]["images"] == ["8", 0]
    assert "33" not in workflow
    assert "35" not in workflow
    assert all(node["class_type"] != "FaceDetailer" for node in workflow.values())


def test_ultra_detail_appends_face_and_hand_detailers() -> None:
    avatar = load_avatar("sophie_bennet")
    positive = compose_positive(avatar, _SCENE)
    workflow = build_workflow(
        avatar,
        avatar.preset("ultra_detail"),
        positive=positive,
        negative=None,
        seed=11,
        filename_prefix="sophie_ultra_11",
    )
    assert workflow["7"]["inputs"]["model"] == ["25", 0]
    assert workflow["7"]["inputs"]["steps"] == 30
    assert workflow["40"]["class_type"] == "LoraLoaderModelOnly"
    assert workflow["40"]["inputs"]["model"] == ["1", 0]
    assert workflow["40"]["inputs"]["strength_model"] == pytest.approx(0.85)
    assert workflow["40"]["inputs"]["lora_name"] == avatar.identity_lora.filename
    assert workflow["30"]["inputs"]["model_name"] == "bbox/face_yolov8m.pt"
    assert workflow["31"]["inputs"]["model_name"] == "bbox/hand_yolov8s.pt"
    assert workflow["33"]["class_type"] == "FaceDetailer"
    assert workflow["33"]["inputs"]["denoise"] == pytest.approx(0.48)
    assert workflow["33"]["inputs"]["model"] == ["40", 0]
    assert workflow["35"]["class_type"] == "FaceDetailer"
    assert workflow["35"]["inputs"]["denoise"] == pytest.approx(0.38)
    assert workflow["35"]["inputs"]["image"] == ["33", 0]
    assert workflow["9"]["inputs"]["images"] == ["35", 0]
    assert "perfection_style" not in workflow["40"]["inputs"]["lora_name"]


def test_prompt_policy_rejects_identity_drift_and_unnamed_garments() -> None:
    avatar = load_avatar("sophie_bennet")
    with pytest.raises(PromptPolicyError):
        compose_positive(avatar, _SCENE.replace("relaxed", "freckles and relaxed"))
    with pytest.raises(PromptPolicyError):
        compose_positive(avatar, "blonde, " + _SCENE)
    with pytest.raises(PromptPolicyError):
        compose_positive(
            avatar,
            "calm smile, waist-up portrait facing the camera, wearing a white t-shirt",
        )
    with pytest.raises(PromptPolicyError):
        compose_positive(avatar, "topless, " + _SCENE)


def test_shipped_templates_pass_policy() -> None:
    avatar = load_avatar("sophie_bennet")
    library = load_templates(default_template_path(avatar))
    names = library.set_names()
    assert names == [
        "beach_bikini",
        "fashion",
        "fitness",
        "lifestyle",
        "lingerie_boudoir",
    ]
    from models_avatar.prompts import render_scene

    count = 0
    for template in library.templates_for("all"):
        text = compose_positive(avatar, render_scene(library, template))
        assert text.startswith("Sophie Bennet,")
        assert ", unedited candid photo," in text
        assert "wearing" in text
        count += 1
    assert count == 20
