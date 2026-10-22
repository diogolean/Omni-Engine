# -*- coding: utf-8 -*-
from __future__ import annotations

from production.pipeline.runtime import (
    Any,
    BatchAngle,
    GeminiImageAdapter,
    PageContext,
    Path,
    PlannedModels,
    B2StorageCapError,
    B2VideoUploader,
    BatchUniquenessGuard,
    CaptionEngine,
    ChannelFactory,
    CostTracker,
    MAX_UNIQUENESS_RETRIES,
    PENDING_CAPTION,
    ThreadPoolExecutor,
    VisualArchitect,
    _MEI_DNA_FALLBACK,
    _SEQUENCE_VIDEO_POST_TYPES,
    _assert_mei_avatar,
    _classify_mei_visual_theme,
    _re,
    _resolve_mei_avatar_path,
    _brand_apply_logo,
    _brand_apply_text,
    _brand_burn_video,
    _brand_text_quote_bg,
    _build_hook_body_act_durations,
    _compute_dense_act_count,
    _compute_hook_body_act_count,
    _core_compile_sequence_reel,
    _describe_scene_plan,
    _plan_scenes,
    _recent_library_topics,
    _scale_scene_durations,
    _segment_script_into_act_snippets,
    _topic_theme_key,
    app_config,
    append_entry,
    append_planner_row,
    append_postplanner_xlsx_row,
    apply_voice_loudnorm,
    as_completed,
    assert_voice_and_subtitles_ready,
    build_carousel_slide_prompt,
    build_gemini_researcher_instruction,
    build_library_metadata,
    build_smart_bait_image_prompt,
    compile_dynamic_reel,
    contextual_cta_keyword,
    datetime,
    dump_raw_research_to_log,
    economic_humanizer_instruction_preview,
    functools,
    generate_ambient_track,
    generate_bulk_topics,
    generate_master_mei_soundscape,
    generate_voiceover_with_timestamps,
    get_image_adapter,
    ground_image_concept,
    humanizer_preview_with_placeholder,
    imagine_subject,
    imagine_subject_instruction_preview,
    list_pdf_relative_paths,
    load_digital_product_corpus,
    logging,
    long_caption_prompt_preview,
    maybe_inject_horror_mutation,
    merge_update_json,
    os,
    page_outputs_dir,
    path_under_engine,
    pipeline_logs_dir,
    plan_angles_matrix,
    plan_distinct_batch_topics,
    scheduled_bulk_post_display,
    select_distinct_pool_topics,
    subject_slug,
    threading,
    time,
    timezone,
    upload_image_file_to_imgbb,
    write_atomic_json,
)
from production.pipeline.log import _LOG
from production.pipeline.bootstrap import REPO_ROOT
from production.pipeline.acts import _build_master_mei_script_act_prompts, _build_reel_act_descriptors, _extract_topic_visual_entities, _is_logo_only_still, _is_sequence_video_post, _load_engagement_bait_examples, _planner_media_url, _silence_noisy_http_loggers, _split_script_into_act_chunks, _strip_audio_behavior_tags  # noqa: E402
from production.pipeline.reporting import _bootstrap_pipeline_intro, _print_video_cost_summary, _snapshot_verified_models  # noqa: E402
from production.pipeline.stills import _apply_still_cache_to_jobs, _ensure_sequence_image, _export_scene_assets_sequential, _find_cached_sequence_still, _maybe_convert_to_video, _run_acts_parallel, _track_adapter_image, _track_remote_gpu_batch  # noqa: E402
from production.pipeline.voice import _commit_reel_angle, _filter_audio_tag_timings, _reel_audio_is_f5, _stitch_audio_sequential, _synthesize_sequence_voice_track, _trim_script_to_word_limit  # noqa: E402

"""Agentic stills path, unchanged from the orchestrator."""

def _produce_agentic(
    *,
    resolved_subject: str,
    qty: int,
    skip_image: bool,
    skip_caption: bool,
    page_ctx: PageContext | None,
    post_type: str,
    post_format: str,
    caption_engine: "CaptionEngine | None",
    per_variant_topics: "list[str] | None",
    envelope_base: dict[str, Any],
    carousel_quantity: int = 3,
) -> dict[str, Any]:
    """Run PipelineOrchestrator and map results into a produce() envelope."""
    from production.pipeline.agentic import PipelineOrchestrator
    from production.pipeline.agentic.criteria import MAX_RETRIES as _AK_MAX_RETRIES

    page_id = page_ctx.page_id if page_ctx else app_config.ACTIVE_PAGE
    out_dir = app_config.ASSETS_DIR / "agentic"

    # ── CostTracker — build FIRST and share into the orchestrator so every node
    # self-reports token/image costs accurately (no post-hoc heuristic guessing).
    _cost_tracker: CostTracker | None = None
    _track_on = bool(page_ctx is None or page_ctx.enable_cost_tracking)
    if _track_on:
        try:
            _cost_tracker = CostTracker(
                page_id=page_id or "ancient_knowledge",
                cost_tier=(page_ctx.cost_tier if page_ctx else "nano"),
            )
        except Exception as cost_init_exc:  # noqa: BLE001
            _LOG.warning("CostTracker init failed on agentic path (%s)", cost_init_exc)
            _cost_tracker = None

    orch = PipelineOrchestrator(
        channel_id=page_id or "ancient_knowledge",
        output_dir=out_dir,
        max_retries=_AK_MAX_RETRIES,
        max_workers=min(3, max(1, qty)),
        caption_engine=caption_engine,
        skip_image=skip_image,
        skip_caption=skip_caption,
        post_type=post_type,
        cost_tracker=_cost_tracker,
    )
    envelope = orch.produce_envelope(
        quantity=qty,
        seed_topic=resolved_subject,
        per_slot_topics=per_variant_topics,
        page_id=page_id,
        post_type=post_type,
        post_format=post_format,
    )
    envelope.update({k: v for k, v in envelope_base.items() if k not in envelope})
    envelope["resolved_subject"] = resolved_subject

    items = envelope.get("items") or []
    if not isinstance(items, list):
        items = []

    if _cost_tracker is not None:
        try:
            # Trust the node-reported ledger; just snapshot it into the envelope.
            envelope["final_cost_breakdown"] = _cost_tracker.to_dict()
            envelope["estimated_cost_usd"] = round(_cost_tracker.pipeline_usd(), 6)
            if _track_on:
                _cost_tracker.write_telemetry(
                    app_config.LIBRARY_DIR, variant_index=max(1, len(items)),
                )
            _LOG.info(
                "Agentic CostTracker | ops=%s total=$%.6f tier=%s",
                len(_cost_tracker.to_dict().get("breakdown") or []),
                _cost_tracker.pipeline_usd(),
                _cost_tracker.cost_tier,
            )
        except Exception as cost_exc:  # noqa: BLE001
            _LOG.warning("Agentic cost snapshot failed (%s) — continuing with $0 fallback", cost_exc)
            envelope.setdefault("final_cost_breakdown", {
                "page_id": page_id,
                "cost_tier": page_ctx.cost_tier if page_ctx else "nano",
                "total_estimated_usd": 0.0,
                "breakdown": [],
            })

    key_ib = app_config.IMGBB_API_KEY
    for row in items:
        img = row.get("local_image_path") or ""
        img_path = Path(img) if img else None
        if key_ib and img_path is not None and img_path.is_file():
            try:
                row["imgbb_url"] = upload_image_file_to_imgbb(key_ib, img_path) or ""
            except Exception as up_exc:  # noqa: BLE001
                _LOG.warning("ImgBB upload failed for agentic still (%s)", up_exc)
                row["imgbb_url"] = ""
        else:
            row.setdefault("imgbb_url", "")
        if _cost_tracker is not None:
            row.setdefault("cost_breakdown", _cost_tracker.to_dict())
            row.setdefault("estimated_cost_usd", round(_cost_tracker.pipeline_usd(), 6))
        try:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
            app_config.LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
            durable_abs = app_config.LIBRARY_DIR / f"agentic_{stamp}_v{row.get('variant_index', 0):02d}.json"
            payload = {
                "page_id": page_id,
                "pipeline": "agentic_4tier",
                "post_type": post_type,
                "topic": row.get("theme") or resolved_subject,
                "caption": row.get("caption") or "",
                "overlay_text": row.get("overlay_text") or "",
                "image_prompt": row.get("image_prompt") or "",
                "local_image_path": row.get("local_image_path") or "",
                "imgbb_url": row.get("imgbb_url") or "",
                "qa_status": row.get("qa_status") or "",
                "qa_feedback": row.get("qa_feedback") or "",
                "qa_score": row.get("qa_score") or 0.0,
                "retry_count": row.get("retry_count") or 0,
                "pinterest_title": row.get("pinterest_title") or "",
                "pinterest_description": row.get("pinterest_description") or "",
                "created_utc": datetime.now(timezone.utc).isoformat(),
            }
            if _cost_tracker is not None:
                _cost_tracker.annotate_payload(payload)
            write_atomic_json(durable_abs, payload)
            row["library_json_relative"] = path_under_engine(
                app_config.ENGINE_ROOT, durable_abs,
            )
        except Exception as dur_exc:  # noqa: BLE001
            _LOG.warning("Agentic durable JSON skipped (%s)", dur_exc)

        try:
            from production.library.asset_library import register_generated_asset

            _qa = str(row.get("qa_status") or "").lower()
            _img = row.get("local_image_path") or row.get("image_path") or ""
            if _img and _qa == "approved":
                register_generated_asset(
                    channel=str(page_id or "ancient_knowledge"),
                    post_type=str(post_type or "LONG_CAPTION_IMAGE"),
                    local_path=str(_img),
                    remote_url=str(row.get("imgbb_url") or ""),
                    prompt=str(row.get("image_prompt") or ""),
                    caption=str(row.get("caption") or ""),
                    platform="facebook",
                )
        except Exception as lib_exc:  # noqa: BLE001
            _LOG.warning("asset_library register skipped (%s)", lib_exc)

    _persist_agentic_postplanner(
        items=items,
        page_ctx=page_ctx,
        post_type=post_type,
    )

    snippet_lines = []
    for idx, row in enumerate(items):
        cap = row.get("caption")
        if isinstance(cap, str) and cap.strip():
            snippet_lines.append(f"{idx + 1}. {cap}")
    snippet_path = app_config.PAGE_OUTPUTS_DIR / "last_captions_bundle.txt"
    if snippet_lines:
        try:
            snippet_path.parent.mkdir(parents=True, exist_ok=True)
            snippet_path.write_text("\n".join(snippet_lines) + "\n", encoding="utf-8")
        except OSError as snip_exc:
            _LOG.warning("last_captions_bundle.txt write failed (%s)", snip_exc)

    approved = envelope.get("successful") or 0
    print(
        f"\n[agentic] Done | approved={approved}/{qty} | "
        f"images={envelope.get('total_images_generated', 0)} | "
        f"out={out_dir}"
    )
    _LOG.info(
        "AGENTIC PIPELINE DONE | page=%s | approved=%s/%s | dir=%s",
        page_id, approved, qty, out_dir,
    )
    return envelope
def _persist_agentic_postplanner(
    *,
    items: list[dict[str, Any]],
    page_ctx: PageContext | None,
    post_type: str,
) -> None:
    """Write bulk + timestamped PostPlanner xlsx rows for agentic stills."""
    if not items:
        _LOG.info("PostPlanner | agentic persist skipped (no items)")
        return
    run_stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    bulk_path = Path(app_config.POST_PLANNER_XLSX).expanduser().resolve()
    postplanner_dir = Path(app_config.PAGE_OUTPUTS_DIR).expanduser().resolve() / "postplanner"
    template = getattr(app_config, "BULK_POSTS_TEMPLATE_XLSX", None)
    planner_type = "VIDEO" if str(post_type).upper().endswith("REEL") else "IMAGE"
    written = 0
    for row in items:
        caption = row.get("caption") or ""
        if not str(caption).strip():
            continue
        media = _planner_media_url(
            post_type,
            imgbb_url=str(row.get("imgbb_url") or ""),
            local_image=str(row.get("local_image_path") or row.get("image_path") or ""),
        )
        variant_ix = max(0, int(row.get("variant_index") or 1) - 1)
        posting_time = scheduled_bulk_post_display(variant_index=variant_ix)
        try:
            planner_row_ix = append_planner_row(
                bulk_path,
                posting_time=posting_time,
                caption=str(caption),
                media_url=str(media),
                post_type_value=planner_type,
                template_path=template,
            )
            row["excel_row"] = planner_row_ix
        except Exception as bulk_exc:  # noqa: BLE001
            _LOG.error(
                "PostPlanner bulk workbook write failed (variant %s) | path=%s | %s",
                row.get("variant_index"), bulk_path, bulk_exc,
            )
        try:
            xlsx_path = append_postplanner_xlsx_row(
                postplanner_dir,
                run_stamp=run_stamp,
                posting_time=posting_time,
                caption=str(caption),
                media_url=str(media),
            )
            row["postplanner_xlsx"] = str(xlsx_path)
            written += 1
        except Exception as xlsx_exc:  # noqa: BLE001
            _LOG.error(
                "PostPlanner timestamped xlsx write failed (variant %s) | dir=%s | %s",
                row.get("variant_index"), postplanner_dir, xlsx_exc,
            )
    _LOG.info(
        "PostPlanner agentic persist | rows=%s | bulk=%s | dir=%s",
        written, bulk_path, postplanner_dir,
    )
