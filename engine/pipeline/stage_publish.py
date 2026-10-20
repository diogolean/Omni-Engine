# -*- coding: utf-8 -*-
"""Uploads, planner rows, library writes, and cost telemetry."""
from __future__ import annotations

from engine.pipeline.state import VariantState
from engine.runtime import (
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
from engine.log import _LOG
from engine.bootstrap import REPO_ROOT
from engine.acts import _build_master_mei_script_act_prompts, _build_reel_act_descriptors, _extract_topic_visual_entities, _is_logo_only_still, _is_sequence_video_post, _load_engagement_bait_examples, _planner_media_url, _silence_noisy_http_loggers, _split_script_into_act_chunks, _strip_audio_behavior_tags  # noqa: E402
from engine.reporting import _bootstrap_pipeline_intro, _print_video_cost_summary, _snapshot_verified_models  # noqa: E402
from engine.stills import _apply_still_cache_to_jobs, _ensure_sequence_image, _export_scene_assets_sequential, _find_cached_sequence_still, _maybe_convert_to_video, _run_acts_parallel, _track_adapter_image, _track_remote_gpu_batch  # noqa: E402
from engine.voice import _commit_reel_angle, _filter_audio_tag_timings, _reel_audio_is_f5, _stitch_audio_sequential, _synthesize_sequence_voice_track, _trim_script_to_word_limit  # noqa: E402


def _stage_publish_01(state: VariantState) -> None:
    """Variant worker stage. Original lines 4643-4963."""
    if state.post_type in ('SMART_BAIT', 'ECONOMIC_REEL', 'WAN_REEL') and (not state.skip_caption):
        if state.caption_mode_tag != 'researcher_fallback' and (state.overlay_text or state.caption):
            state.stamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')
            state.durable_fname = f'post_{state.stamp}_v{state.variant + 1:02d}.json'
            state.durable_abs = app_config.LIBRARY_DIR / state.durable_fname
            state.created_iso = datetime.now(timezone.utc).isoformat()
            state.smart_bait_payload = {'page_id': state.page_ctx.page_id if state.page_ctx else app_config.ACTIVE_PAGE, 'avatar_mode': state.avatar_mode, 'post_format': state.post_format, 'post_type': state.post_type, 'topic': state.resolved_subject, 'subject_slug': state.slug, 'variant_index': state.variant + 1, 'quantity_total': state.qty, 'economic_brain_mode': state.economic, 'image_relative': state.img_ref_engine, 'video_path': str(state.reel_path) if state.reel_path else state.video_path_str, 'b2_url': state._b2_video_url, 'imgbb_url': state.imgbb_url, 'overlay_text': state.overlay_text, 'humanized_caption': state.caption, 'caption_status': state.caption_mode_tag, 'created_utc': state.created_iso, 'qa_verdict': (state._qa_report or {}).get('verdict') or '', 'qa_report_path': (state._qa_report or {}).get('report_path') or '', 'publishable': bool((state._qa_report or {}).get('publishable'))}
            if (state.page_ctx.page_id if state.page_ctx else '').lower() == 'master_mei':
                from agents.media.mei_narrative import build_seo_description, build_seo_title
                state.build_seo_description = build_seo_description
                state.build_seo_title = build_seo_title
                state._llm_seo = getattr(state.caption_engine, 'last_seo_title', '') if state.caption_engine else ''
                state.smart_bait_payload['title'] = (state._llm_seo or state.build_seo_title(state.resolved_subject)).strip()[:100]
                state.smart_bait_payload['description'] = state.build_seo_description(state.resolved_subject, caption=state.caption or '')
            write_atomic_json(state.durable_abs, state.smart_bait_payload)
            try:
                with state.write_lock:
                    state._planner_type = 'VIDEO' if _is_sequence_video_post(state.post_type) else 'IMAGE'
                    if _is_sequence_video_post(state.post_type) and state.reel_path:
                        state._planner_media = state._b2_video_url or str(state.reel_path)
                    else:
                        state._planner_media = state.imgbb_url
                    from modules.reel_qa.publish import planner_row_allowed
                    state.planner_row_allowed = planner_row_allowed
                    if not state.planner_row_allowed(post_type=state.post_type, report=state._qa_report):
                        _LOG.error('REEL QA blocked Post Planner | post_type=%s verdict=%s', state.post_type, (state._qa_report or {}).get('verdict'))
                        print(f'[VisualQA] Post Planner skipped | verdict={(state._qa_report or {}).get('verdict') or 'missing'}')
                    else:
                        state.planner_row_ix = append_planner_row(app_config.POST_PLANNER_XLSX, posting_time=state.posting_slot_display, caption=state.caption, url_link='', media_url=state._planner_media, post_type_value=state._planner_type, template_path=app_config.BULK_POSTS_TEMPLATE_XLSX)
            except Exception as fin_exc:
                state.fin_exc = fin_exc
                _LOG.warning('Smart bait / reel planner write failed (variant %s): %s', state.variant + 1, state.fin_exc)
    elif not state.skip_caption:
        if state.caption_mode_tag == 'researcher_fallback':
            _LOG.warning("All humanizers failed for variant %s of '%s'. Variant skipped in Excel/CSV; research saved to outputs/logs/.", state.variant + 1, state.resolved_subject)
            merge_update_json(state.durable_abs, {'humanized_caption': '', 'caption_status': 'skipped_humanizer_failure', 'humanized_utc': datetime.now(timezone.utc).isoformat(), 'imgbb_url': state.imgbb_url})
        else:
            if state.caption_mode_tag == 'gemini_fallback':
                _LOG.info('Claude failed; Gemini fallback succeeded for variant %s.', state.variant + 1)
            state.caption_payload = {'humanized_caption': state.caption, 'caption_status': state.caption_mode_tag, 'humanized_utc': datetime.now(timezone.utc).isoformat(), 'imgbb_url': state.imgbb_url}
            try:
                if state.durable_abs is not None:
                    merge_update_json(state.durable_abs, state.caption_payload)
                else:
                    _LOG.debug('durable_abs is None (avatar OFF or durable write skipped) — skipping merge_update_json for variant %s; Excel write continues.', state.variant + 1)
                with state.write_lock:
                    state.planner_row_ix = append_planner_row(app_config.POST_PLANNER_XLSX, posting_time=state.posting_slot_display, caption=state.caption, url_link='', media_url=state.imgbb_url, post_type_value='IMAGE', template_path=app_config.BULK_POSTS_TEMPLATE_XLSX)
            except Exception as fin_exc:
                state.fin_exc = fin_exc
                _LOG.warning('Post-humanizer durable/Excel write failed (variant %s): %s', state.variant + 1, state.fin_exc, exc_info=True)
    if state.skip_caption:
        state.caption_mode_tag = 'skipped'
        with state.write_lock:
            state.planner_row_ix = append_planner_row(app_config.POST_PLANNER_XLSX, posting_time=state.posting_slot_display, caption=state.caption if isinstance(state.caption, str) else '(skipped)', url_link='', media_url=state.imgbb_url, post_type_value='IMAGE', template_path=app_config.BULK_POSTS_TEMPLATE_XLSX)
    logging.info('--- VARIANT %s | TOPIC `%s` ---', state.variant + 1, state.resolved_subject)
    logging.info('RAW FACT SHEET (researcher)\n%s', state.raw_sheet)
    logging.info('FINAL CAPTION (humanizer)\n%s', state.caption)
    if isinstance(state.raw_sheet, str) and state.raw_sheet not in ('(skipped)', ''):
        try:
            dump_raw_research_to_log(state.logs_dir, run_stamp=state.run_stamp, topic=state.resolved_subject, variant_index=state.variant + 1, raw_fact_sheet=state.raw_sheet)
        except Exception as log_exc:
            state.log_exc = log_exc
            _LOG.warning('Research log write failed: %s', state.log_exc)
    state.caption_str = state.caption if isinstance(state.caption, str) else ''
    try:
        from agents.media.free_images.attribution import append_credit, credits_for_images
        state.append_credit = append_credit
        state.credits_for_images = credits_for_images
        state._credit_paths = []
        if state.img_path_display:
            state._credit_paths.append(state.img_path_display)
        state._credit_paths.extend(list(getattr(state, '_sequence_image_paths', None) or []))
        state._image_credit = state.credits_for_images(state._credit_paths)
        if state._image_credit:
            state.caption_str = state.append_credit(state.caption_str, state._image_credit, limit=2000)
    except Exception:
        pass
    state.meta = {}
    with state.write_lock:
        state.meta = append_entry(app_config.CONTENT_LIBRARY_PATH, build_library_metadata(topic=state.resolved_subject, final_caption=state.caption_str, imgbb_url=state.imgbb_url, video_path=state.video_path_str or '', carousel_images=state._carousel_imgbb if state.post_type == 'CAROUSEL' else None, channel_id=getattr(app_config, 'ACTIVE_PAGE', '') or '', post_type=state.post_type or '', title=state._seo_title or state.overlay_text or state.resolved_subject or ''))
    from modules.reel_qa.publish import write_postplanner_xlsx_if_allowed
    state.write_postplanner_xlsx_if_allowed = write_postplanner_xlsx_if_allowed

    def _write_timestamped_xlsx() -> None:
        xlsx_path: Path | None = None
        with state.write_lock:
            xlsx_path = append_postplanner_xlsx_row(state.postplanner_dir, run_stamp=state.run_stamp, posting_time=state.posting_slot_display, caption=state.caption_str, media_url=_planner_media_url(state.post_type, imgbb_url=state.imgbb_url, local_image=str(state.img_path_display or ''), b2_video_url=state._b2_video_url, video_path=state.video_path_str))
        if xlsx_path:
            _LOG.info('PostPlanner XLSX row written: %s', xlsx_path.name)
    try:
        if not state.write_postplanner_xlsx_if_allowed(post_type=state.post_type, report=state._qa_report, caption_mode=state.caption_mode_tag, write=_write_timestamped_xlsx) and state.caption_mode_tag not in ('researcher_fallback', 'skipped'):
            _LOG.info('PostPlanner XLSX skipped | QA blocked | verdict=%s', (state._qa_report or {}).get('verdict'))
    except Exception as xlsx_exc:
        state.xlsx_exc = xlsx_exc
        _LOG.warning('PostPlanner XLSX write failed (variant %s): %s', state.variant + 1, state.xlsx_exc)
    if state.skip_image:
        state.img_report = '(skipped)'
    elif state.adapter is not None and state.adapter.last_gemini_image_model_used:
        state.img_report = state.adapter.last_gemini_image_model_used
    else:
        state.img_report = state.bm.image_primary_id
    state.lib_json_rel = path_under_engine(app_config.ENGINE_ROOT, state.durable_abs) if state.durable_abs else ''
    state._seo_title = ''
    state._seo_description = state.caption_str
    if (state.page_ctx.page_id if state.page_ctx else '').lower() == 'master_mei':
        from agents.media.mei_narrative import build_seo_description, build_seo_title
        state.build_seo_description = build_seo_description
        state.build_seo_title = build_seo_title
        state._llm_seo = state._seo_title_local or (getattr(state.caption_engine, 'last_seo_title', '') if state.caption_engine else '')
        if state._batch_angle and state._batch_angle.seo_title_hint and (not state._llm_seo):
            state._seo_title = state._batch_angle.seo_title_hint.strip()[:100]
        else:
            state._seo_title = (state._llm_seo or state.build_seo_title(state.resolved_subject)).strip()[:100]
        state._seo_description = state.build_seo_description(state.resolved_subject, caption=state.caption_str or '')
    elif state._batch_angle is not None:
        state._seo_title = (state._seo_title_local or (state._batch_angle.seo_title_hint or state._batch_angle.angle_title) or state.overlay_text or state.resolved_subject).strip()[:100]
        state._angle_lead = f'{state._batch_angle.angle_title}: {state._batch_angle.angle_brief}'.strip()
        if state.caption_str:
            state._seo_description = f'{state._angle_lead}\n\n{state.caption_str}'
        else:
            state._seo_description = state._angle_lead
    elif state._seo_title_local:
        state._seo_title = state._seo_title_local[:100]
    state._return_dict = {'topic': state.resolved_subject, 'title': state._seo_title or state.overlay_text or state.resolved_subject, 'batch_angle': state._batch_angle.angle_title if state._batch_angle else '', 'hook_style': state._batch_angle.hook_style if state._batch_angle else '', 'variant_index': state.variant + 1, 'local_image_path': state.img_ref_engine, 'post_type': state.post_type, 'video_path': state.video_path_str, 'qa_verdict': (state._qa_report or {}).get('verdict') or '', 'qa_report_path': (state._qa_report or {}).get('report_path') or '', 'imgbb_url': state.imgbb_url, 'caption': state.caption_str, 'description': state._seo_description, 'overlay_text': state.overlay_text, 'library_timestamp': state.meta.get('timestamp'), 'library_json_relative': state.lib_json_rel, 'excel_row': state.planner_row_ix, 'model_image_used': state.img_report, 'model_research_head': state.bm.research_primary_id, 'humanizer': state.humanizer_notes, 'caption_mode': state.caption_mode_tag if state.caption_mode_tag is not None else 'skipped', 'pinterest_title': getattr(state.caption_engine, 'last_pinterest_title', '') if state.caption_engine else '', 'pinterest_description': getattr(state.caption_engine, 'last_pinterest_description', '') if state.caption_engine else '', 'carousel_image_paths': state._carousel_image_paths, 'carousel_imgbb': state._carousel_imgbb, 'images_generated': state._images_generated_this_variant, 'wan_act_status': list(state._wan_act_status or []), 'wan_stage_times': dict(state._wan_stage_times or {}), 'wan_variant_wall_s': round(time.monotonic() - state._wan_t0_variant, 2) if state.post_type == 'WAN_REEL' else None}
    if state.cost_tracker is not None and state.page_ctx is not None and state.page_ctx.enable_cost_tracking:
        state.cost_tracker.write_telemetry(app_config.LIBRARY_DIR, variant_index=state.variant + 1)
        state._pipeline_usd = state.cost_tracker.pipeline_usd()
        state._return_dict['estimated_cost_usd'] = round(state._pipeline_usd, 6)
        state._return_dict['cost_tier'] = state.cost_tracker.cost_tier
        state._return_dict['cost_breakdown'] = state.cost_tracker.to_dict()
        _LOG.info('CostTracker | variant %d pipeline=$%.6f ledger=$%.6f tier=%s', state.variant + 1, state._pipeline_usd, state.cost_tracker.total_usd(), state.cost_tracker.cost_tier)
        if state.batch_cost_tracker is not None:
            try:
                state.batch_cost_tracker.merge(state.cost_tracker)
            except Exception as _mg:
                state._mg = _mg
                _LOG.debug('CostTracker batch merge skipped (%s)', state._mg)
