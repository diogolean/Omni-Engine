# -*- coding: utf-8 -*-
"""The engine split keeps entrypoints, flags, and env vars."""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# The pipeline implementation. channel_context, page_profile, and the agentic
# package were not part of the engine/ split this test locks.
ENGINE = ROOT / "production" / "pipeline"
_NOT_ENGINE = {"channel_context.py", "page_profile.py", "post_type_registry.py"}


def _engine_sources() -> list[Path]:
    files = [path for path in ENGINE.glob("*.py") if path.name not in _NOT_ENGINE]
    files.extend((ENGINE / "stages").rglob("*.py"))
    return files

# Parameter names of the public functions before the split.
ENTRY_PARAMS = {
    "produce": (
        ["subject"],
        [
            "quantity",
            "skip_image",
            "skip_caption",
            "test_mode",
            "economic_brain_mode",
            "bootstrap_models",
            "page_ctx",
            "cta_enabled",
            "post_type",
            "carousel_quantity",
            "image_style",
            "render_approval_required",
            "agentic_pipeline",
            "multi_variant",
            "text_overlay",
        ],
    ),
    "run_pipeline": (["topic"], ["skip_image", "skip_caption", "test_mode"]),
    "_produce_variant_worker": (
        ["variant"],
        [
            "qty",
            "slug",
            "resolved_subject",
            "corpus",
            "pre_narratives",
            "caption_engine",
            "skip_image",
            "skip_caption",
            "avatar_mode",
            "post_format",
            "atmosphere_style",
            "page_aspect_ratio",
            "effective_ref_path",
            "economic",
            "econ_model",
            "bm",
            "page_ctx",
            "subject_assets",
            "run_stamp",
            "postplanner_dir",
            "logs_dir",
            "write_lock",
            "cta_enabled",
            "post_type",
            "carousel_quantity",
            "image_style",
            "text_overlay_enabled",
            "generated_hooks_cache",
            "hooks_cache_lock",
            "hooks_cache_path",
            "cost_tracker",
            "per_variant_topics",
            "batch_angles",
            "uniqueness_guard",
            "global_topic_dna",
            "batch_image_counter",
            "batch_image_lock",
            "render_approval_required",
        ],
    ),
    "cli": ([], []),
}

CLI_DESTS = {
    "topic",
    "ignore_reel_checkpoints",
    "clear_reel_checkpoints",
    "hook_parallax",
    "quantity",
    "carousel_quantity",
    "multi_variant",
    "text_overlay",
    "page",
    "avatar",
    "post_format",
    "skip_image",
    "skip_caption",
    "agentic_pipeline",
    "test",
    "debug",
    "cta",
    "post_type",
    "duration",
    "module",
    "lofi_theme",
    "lofi_subtheme",
    "lofi_mode",
    "lofi_seed_quote",
    "lofi_image_provider",
    "lofi_aphorism_id",
    "lofi_scripts",
    "lofi_allow_mixed_era",
    "stills_only",
    "script_only",
    "dry_run",
    "lofi_no_review",
    "lofi_resume_from",
    "lofi_approve_gate",
    "test_preview",
    "prompt",
    "economic",
    "premium",
    "publish_youtube",
    "yt_privacy",
    "schedule_uploads",
    "interval_hours",
    "random_delay_max_minutes",
    "yt_upload_limit",
    "draw_style",
    "clip_duration",
    "render_approval_required",
    "test_images",
    "resume_youtube_queue",
    "model_api_flow",
    "img_production",
    "image_primary",
    "together_juggernaut",
    "together_flux2dev",
    "deepinfra_flux2dev",
    "image_model",
    "audio_production",
    "video_production",
    "video_length",
    "scene_duration",
    "scene_length",
}

ENV_NAMES = {
    "REEL_ARTIFACT_IMAGES_CHANNELS",
    "WONDER_FEED_STYLE_REFERENCE",
    "ENGINE_DEBUG",
    "LOFI_ALLOW_MIXED_ERA",
    "LOFI_FLUX_BACKEND",
    "REEL_CHECKPOINT",
    "REEL_HOOK_PARALLAX",
    "TOGETHER_IMAGE_MODEL",
}


def _params(fn: object) -> tuple[list[str], list[str]]:
    signature = inspect.signature(fn)
    positional = [
        name
        for name, param in signature.parameters.items()
        if param.kind in (param.POSITIONAL_ONLY, param.POSITIONAL_OR_KEYWORD)
    ]
    keyword = [
        name
        for name, param in signature.parameters.items()
        if param.kind is param.KEYWORD_ONLY
    ]
    return positional, keyword


def test_engine_files_stay_under_a_thousand_lines() -> None:
    offenders = []
    for path in _engine_sources():
        count = len(path.read_text(encoding="utf-8").splitlines())
        if count > 1000:
            offenders.append(f"{path.relative_to(ROOT)}:{count}")
    assert offenders == []


def test_public_signatures_match_the_pre_split_functions() -> None:
    from engine.cli import cli
    from engine.orchestrator import _produce_variant_worker, produce, run_pipeline

    found = {
        "produce": _params(produce),
        "run_pipeline": _params(run_pipeline),
        "_produce_variant_worker": _params(_produce_variant_worker),
        "cli": _params(cli),
    }
    assert found == ENTRY_PARAMS


def test_main_reexports_the_same_objects() -> None:
    import main
    from engine.cli import _together_cli_image_model, cli
    from engine.orchestrator import (
        _persist_agentic_postplanner,
        _produce_agentic,
        _produce_variant_worker,
        produce,
        run_pipeline,
        run_test_images_debug_mode,
    )

    assert main.produce is produce
    assert main.run_pipeline is run_pipeline
    assert main._produce_variant_worker is _produce_variant_worker
    assert main._produce_agentic is _produce_agentic
    assert main._persist_agentic_postplanner is _persist_agentic_postplanner
    assert main.run_test_images_debug_mode is run_test_images_debug_mode
    assert main.cli is cli
    assert main._together_cli_image_model is _together_cli_image_model


def test_cli_destinations_are_unchanged() -> None:
    from engine.cli_parser import parse_cli_args

    args = parse_cli_args([])
    assert CLI_DESTS <= set(vars(args))


def test_split_modules_still_name_the_same_env_vars() -> None:
    blob = "\n".join(path.read_text(encoding="utf-8") for path in _engine_sources())
    missing = sorted(name for name in ENV_NAMES if name not in blob)
    assert missing == []


def test_worker_source_keeps_preflight_pool_and_unique_stills_order() -> None:
    from tests.test_reel_voice_mux_and_sentence_beats import _ordered_variant_source

    source = _ordered_variant_source()
    preflight = source.find("_preflight_text(")
    pool = source.find("_build_topic_pool(")
    stills = source.find("_unique_stills(")
    compile_call = source.find("_core_compile_sequence_reel(")
    assert 0 <= preflight < pool
    assert "ancient_knowledge" in source[preflight : preflight + 80]
    assert "The first sentence must contain" in source
    assert 0 <= stills < compile_call
