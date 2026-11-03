# -*- coding: utf-8 -*-
"""Thin variant worker. Stages run in the original statement order."""
from __future__ import annotations

from production.pipeline.stages.state import VariantState
from production.pipeline.runtime import (
    Any,
    BatchAngle,
    BatchUniquenessGuard,
    CaptionEngine,
    CostTracker,
    PageContext,
    Path,
    PlannedModels,
    threading,
)
from production.pipeline.stages.stage_caption_brand import _stage_caption_brand_01
from production.pipeline.stages.stage_publish import _stage_publish_01
from production.pipeline.stages.stage_setup import _stage_setup_01, _stage_setup_02

def _produce_variant_worker(
    variant: int,
    *,
    qty: int,
    slug: str,
    resolved_subject: str,
    corpus: Any,
    pre_narratives: list[str],
    caption_engine: "CaptionEngine | None",
    skip_image: bool,
    skip_caption: bool,
    avatar_mode: str,
    post_format: str,
    atmosphere_style: str,
    page_aspect_ratio: str,
    effective_ref_path: "Path | None",
    economic: bool,
    econ_model: str,
    bm: "PlannedModels",
    page_ctx: "PageContext | None",
    subject_assets: Path,
    run_stamp: str,
    postplanner_dir: Path,
    logs_dir: Path,
    write_lock: threading.Lock,
    cta_enabled: bool = True,
    post_type: str = "STANDARD_QUOTE",
    carousel_quantity: int = 3,
    image_style: str = "NATURAL",
    text_overlay_enabled: bool | None = None,
    generated_hooks_cache: "list[str] | None" = None,
    hooks_cache_lock: "threading.Lock | None" = None,
    hooks_cache_path: "Path | None" = None,
    cost_tracker: "CostTracker | None" = None,
    per_variant_topics: "list[str] | None" = None,
    batch_angles: "list[BatchAngle] | None" = None,
    uniqueness_guard: "BatchUniquenessGuard | None" = None,
    global_topic_dna: str = "",
    batch_image_counter: "list[int] | None" = None,
    batch_image_lock: "threading.Lock | None" = None,
    render_approval_required: bool = False,
) -> "dict[str, Any]":
    """
    Produce one complete post variant inside a worker thread.

    Handles image generation, ImgBB upload, caption research, humanisation,
    and all durable artefact writes for variant ``variant`` (0-based index).

    File writes that touch shared resources (Excel workbooks,
    content_library.json) are serialised through ``write_lock`` so concurrent
    workers never corrupt each other's output.

    Raises on fatal errors so the ThreadPoolExecutor caller can catch them
    per-future; for qty==1 the exception propagates naturally to cli().
    """
    state = VariantState()
    state.variant = variant
    state.qty = qty
    state.slug = slug
    state.resolved_subject = resolved_subject
    state.corpus = corpus
    state.pre_narratives = pre_narratives
    state.caption_engine = caption_engine
    state.skip_image = skip_image
    state.skip_caption = skip_caption
    state.avatar_mode = avatar_mode
    state.post_format = post_format
    state.atmosphere_style = atmosphere_style
    state.page_aspect_ratio = page_aspect_ratio
    state.effective_ref_path = effective_ref_path
    state.economic = economic
    state.econ_model = econ_model
    state.bm = bm
    state.page_ctx = page_ctx
    state.subject_assets = subject_assets
    state.run_stamp = run_stamp
    state.postplanner_dir = postplanner_dir
    state.logs_dir = logs_dir
    state.write_lock = write_lock
    state.cta_enabled = cta_enabled
    state.post_type = post_type
    state.carousel_quantity = carousel_quantity
    state.image_style = image_style
    state.text_overlay_enabled = text_overlay_enabled
    state.generated_hooks_cache = generated_hooks_cache
    state.hooks_cache_lock = hooks_cache_lock
    state.hooks_cache_path = hooks_cache_path
    state.cost_tracker = cost_tracker
    state.per_variant_topics = per_variant_topics
    state.batch_angles = batch_angles
    state.uniqueness_guard = uniqueness_guard
    state.global_topic_dna = global_topic_dna
    state.batch_image_counter = batch_image_counter
    state.batch_image_lock = batch_image_lock
    state.render_approval_required = render_approval_required
    _stage_setup_01(state)
    _stage_setup_02(state)
    _stage_caption_brand_01(state)
    _stage_publish_01(state)
    return state._return_dict
