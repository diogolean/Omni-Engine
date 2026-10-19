# -*- coding: utf-8 -*-
"""Caption, fonts, logo, and text overlay."""
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

from engine.pipeline.stage_compile_reel import _stage_compile_reel_04, _stage_compile_reel_05

def _stage_caption_brand_01(state: VariantState) -> None:
    """Variant worker stage. Original lines 3275-4636."""
    if state.post_type not in ('SMART_BAIT', 'LONG_CAPTION_IMAGE', 'ECONOMIC_REEL', 'WAN_REEL', 'CAROUSEL') and (not state.skip_caption):
        assert state.caption_engine is not None
        state.caption_mode_tag = 'humanized'
        state.batch_narrative = state.pre_narratives[state.variation_index] if state.pre_narratives and state.variation_index < len(state.pre_narratives) and state.pre_narratives[state.variation_index] else ''
        if state.batch_narrative:
            state.raw_sheet = state.batch_narrative
            _LOG.debug('Variant %s: using pre-computed batch narrative (%d chars).', state.variant + 1, len(state.raw_sheet))
        else:
            try:
                state.raw_sheet = state.caption_engine.synthesize_facts(state.resolved_subject, state.corpus, research_model_override=state.econ_model if state.economic and (not state.caption_engine._deepseek) else None, variation_index=state.variation_index, total_variants=state.qty, economic=state.economic)
            except Exception as rex:
                state.rex = rex
                state.attempted_rid = state.caption_engine.research_primary_id
                _LOG.error('Gemini research failed variant=%s Gemini_head=`%s`', state.variant + 1, state.attempted_rid, exc_info=True)
                logging.error('VARIANT_FAIL | synthesize_facts | variant=%s | err=%s', state.variant + 1, state.rex)
                raise
        state.stamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')
        state.durable_fname = f'post_{state.stamp}_v{state.variant + 1:02d}.json'
        state.durable_abs = app_config.LIBRARY_DIR / state.durable_fname
        state.created_iso = datetime.now(timezone.utc).isoformat()
        state.excel_rel = path_under_engine(app_config.ENGINE_ROOT, app_config.POST_PLANNER_XLSX)
        state.pending_payload = {'page_id': state.page_ctx.page_id if state.page_ctx else app_config.ACTIVE_PAGE, 'avatar_mode': state.avatar_mode, 'post_format': state.post_format, 'topic': state.resolved_subject, 'subject_slug': state.slug, 'variant_index': state.variant + 1, 'quantity_total': state.qty, 'economic_brain_mode': state.economic, 'image_relative': state.img_ref_engine, 'video_path': state.video_path_str, 'imgbb_url': '', 'library_relative': path_under_engine(app_config.ENGINE_ROOT, state.durable_abs), 'excel_relative': state.excel_rel, 'raw_fact_sheet': state.raw_sheet, 'humanized_caption': PENDING_CAPTION, 'caption_status': 'pending', 'created_utc': state.created_iso}
        write_atomic_json(state.durable_abs, state.pending_payload)
        state.caption = ''
        if state.raw_sheet:
            state.caption, state.caption_mode_tag = state.caption_engine.humanize_voice_with_fallback(state.raw_sheet, state.resolved_subject, variation_index=state.variation_index, total_variants=state.qty, cta_keyword=state.cta_kw, economic=state.economic, model_id=state.econ_model if state.economic and (not state.caption_engine._deepseek) else None, cta_enabled=state.cta_enabled, cta_caption_format=state.post_type == 'CTA_CAPTION_IMAGE')
        else:
            state.caption_mode_tag = 'researcher_fallback'
    state.logo_path = state.page_ctx.logo_png if state.page_ctx and state.page_ctx.logo_exists else None
    state._is_long_caption_image = _is_logo_only_still(state.post_type)
    state._is_economic_reel = _is_sequence_video_post(state.post_type)
    state._is_wan_reel = state.post_type == 'WAN_REEL'
    state._page_overlay_flag = None
    if state.page_ctx is not None and 'ENABLE_TEXT_OVERLAY' in (state.page_ctx.page_cfg or {}):
        state._page_overlay_flag = bool(state.page_ctx.page_cfg.get('ENABLE_TEXT_OVERLAY'))
    state._clean_still = state.post_type in ('CAROUSEL', 'LONG_CAPTION_IMAGE', 'CTA_CAPTION_IMAGE')
    if state.text_overlay_enabled is False:
        state._overlay_on = False
    elif state.text_overlay_enabled is True:
        state._overlay_on = True
    elif state._clean_still and state._page_overlay_flag is False:
        state._overlay_on = False
    elif state._clean_still and state._page_overlay_flag is True:
        state._overlay_on = True
    elif state._clean_still or state._is_long_caption_image or state._is_economic_reel:
        state._overlay_on = False
    else:
        state._overlay_on = True
    state._needs_text_overlay = state._overlay_on and (not state._is_economic_reel) and (state.post_format in ('IMAGE_BACKGROUND', 'IMAGE_QUOTE', 'TEXT_QUOTE', 'IMAGE_AVATAR'))
    state._font_size_scale = state.page_ctx.font_size_scale if state.page_ctx else 0.08
    state._font_color = state.page_ctx.font_color if state.page_ctx else (255, 255, 255)
    state._text_outline_width = state.page_ctx.text_outline_width if state.page_ctx else 1
    state._font_path_abs = ''
    if state.page_ctx and state.page_ctx.font_path:
        state._fp_candidate = app_config.ENGINE_ROOT / state.page_ctx.font_path
        if state._fp_candidate.is_file():
            state._font_path_abs = str(state._fp_candidate)
        else:
            _LOG.debug("Page font not found at '%s' — brand_composer will use system pool.", state._fp_candidate)
    state._logo_size_scale = state.page_ctx.logo_size_scale if state.page_ctx else 0.18
    state._logo_position = state.page_ctx.logo_position if state.page_ctx else 'bottom_right'
    if state.raw_bg_path is not None and state.raw_bg_path.is_file():
        if state._is_long_caption_image:
            if state.logo_path:
                state.img_path_display = _brand_apply_logo(state.raw_bg_path, state.logo_path, position=state._logo_position, size_scale=state._logo_size_scale)
        elif state._is_economic_reel:
            state.img_path_display = state.raw_bg_path
        elif state._needs_text_overlay and state.overlay_text:
            state.img_path_display = _brand_apply_text(state.raw_bg_path, state.overlay_text, logo_path=state.logo_path, page_id=state.page_ctx.page_id if state.page_ctx else None, logo_size_scale=state._logo_size_scale, logo_position=state._logo_position, font_path_override=state._font_path_abs or None, font_size_scale=state._font_size_scale, font_color=state._font_color, text_outline_width=state._text_outline_width, post_type=state.post_type)
        elif state.logo_path:
            state.img_path_display = _brand_apply_logo(state.raw_bg_path, state.logo_path, position=state._logo_position, size_scale=state._logo_size_scale)
        if state.video_path_str:
            try:
                state.final_video = _brand_burn_video(Path(state.video_path_str), state.overlay_text, logo_path=state.logo_path)
                state.video_path_str = str(state.final_video)
            except Exception as vexc:
                state.vexc = vexc
                _LOG.warning('Video text burn exception (variant %s): %s', state.variant + 1, state.vexc)
    state.reel_path = None
    state._b2_video_url = ''
    if state._is_economic_reel and state._asset_pool and (len(state._sequence_image_paths) >= 2):
        try:
            from modules.reel_artefact.asset_pool import unique_stills as _unique_stills
            state._unique_stills = _unique_stills
            state._sequence_image_paths, state._stuck_beats = state._unique_stills(list(state._sequence_image_paths), list(state._asset_pool), flux_generate=getattr(state.adapter, 'generate', None) if state.adapter else None, geography=state._pool_geography)
            if state._stuck_beats:
                _LOG.warning('REPEAT GUARD | unresolved beats=%s', state._stuck_beats)
        except Exception as _ug_exc:
            state._ug_exc = _ug_exc
            _LOG.warning('REPEAT GUARD skipped (%s)', type(state._ug_exc).__name__)
    if state._is_economic_reel:
        if state._episode_assets_dir is None:
            state._episode_assets_dir = Path(app_config.ASSETS_DIR) / state._episode_id
            state._episode_assets_dir.mkdir(parents=True, exist_ok=True)
        state._export_sources = list(state._sequence_image_paths) if state._sequence_image_paths else [Path(state.img_path_display)] if state.img_path_display and Path(state.img_path_display).is_file() else []
        if state._export_sources:
            state._exported_scenes = _export_scene_assets_sequential(state._export_sources, state._episode_assets_dir)
            if state._exported_scenes:
                state._assets_dir_disp = str(state._episode_assets_dir.resolve())
                print(f'\n[ASSETS] Episode images saved -> {state._assets_dir_disp}')
                _LOG.info('Scene assets exported → %s (%d files) | episode=%s', state._assets_dir_disp, len(state._exported_scenes), state._episode_id)
                if state._sequence_image_paths:
                    state._sequence_image_paths = list(state._exported_scenes)
                elif state.img_path_display:
                    state.img_path_display = str(state._exported_scenes[0])
                if state.render_approval_required:
                    with state.write_lock:
                        input(f'\n [PAUSED] Images saved to episode assets folder:\n {state._assets_dir_disp}\n Press [ENTER] to approve and continue video rendering, or [Ctrl+C] to abort...\n')
                    _LOG.info('Render approval granted — continuing video compilation (variant %s | episode=%s).', state.variant + 1, state._episode_id)
    if state._reel_qa_block_compile and state._sequence_image_paths:
        _LOG.warning('REEL QA | some beats were hard — compiling the reel anyway')
        state._reel_qa_block_compile = False
    elif state._reel_qa_block_compile:
        _LOG.error('REEL QA | skipping compile — no stills at all')
        print('[VisualQA] compile skipped — no stills')
    if state._is_economic_reel and (not state._reel_qa_block_compile) and (state.img_path_display and Path(state.img_path_display).is_file() or bool(state._sequence_image_paths)):
        _stage_compile_reel_04(state)
        _stage_compile_reel_05(state)
    if state._is_economic_reel:
        from modules.reel_qa.pipeline import seal_compiled_reel
        state.seal_compiled_reel = seal_compiled_reel
        state._qa_beats = list(state._beat_qa_rows)
        if not state._qa_beats:
            state._qa_beats = [{'index': 0, 'text': str(state.overlay_text or state.resolved_subject or ''), 'image_path': str(state.img_path_display or ''), 'passed': None}]
        state._qa_dir = Path(app_config.PAGE_OUTPUTS_DIR) / 'qa'
        state._qa_report = state.seal_compiled_reel(video_path=state.reel_path, post_type=state.post_type, beats=state._qa_beats, word_timings=list(getattr(state, '_word_timings', None) or getattr(state, '_narr_word_timings', None) or []), output_dir=state._qa_dir, stem=f'{state.stem}_v{state.variant + 1:02d}', channel=(state.page_ctx.page_id if state.page_ctx else '') or '', blocked_reason='beat_qa_failed' if state._reel_qa_block_compile else '')
    state.imgbb_url = ''
    state._upload_candidate = state.img_path_display
    if isinstance(state._upload_candidate, Path) and state._upload_candidate.is_file():
        state.key_ib = app_config.IMGBB_API_KEY
        if state.key_ib:
            try:
                state.imgbb_url = upload_image_file_to_imgbb(state.key_ib, state._upload_candidate) or ''
            except Exception as up_exc:
                state.up_exc = up_exc
                _LOG.warning('ImgBB upload exception (%s): %s', state._upload_candidate.name, state.up_exc, exc_info=True)
            if not state.imgbb_url:
                _LOG.warning('ImgBB upload returned empty URL for %s; planner media column stays blank.', state._upload_candidate.name)
        else:
            _LOG.warning('IMGBB_API_KEY missing; CONTENT: MEDIA stays blank.')
    state._carousel_imgbb.clear()
    if state.post_type == 'CAROUSEL' and state._carousel_image_paths:
        state.key_ib = app_config.IMGBB_API_KEY
        for _c_idx, _c_path in enumerate(state._carousel_image_paths, start=1):
            state._c_idx = _c_idx
            state._c_path = _c_path
            state._c_path_obj = Path(state._c_path)
            state._c_url = ''
            if state.imgbb_url and state._c_idx == 1 and (str(Path(state.img_path_display).resolve()) == str(state._c_path_obj.resolve())):
                state._c_url = state.imgbb_url
            elif state.key_ib and state._c_path_obj.is_file():
                try:
                    state._c_url = upload_image_file_to_imgbb(state.key_ib, state._c_path_obj) or ''
                except Exception as _cu:
                    state._cu = _cu
                    _LOG.warning('CAROUSEL imgbb slide %d failed: %s', state._c_idx, state._cu)
            state._carousel_imgbb.append({'index': state._c_idx, 'imgbb_url': state._c_url, 'path_under_engine': path_under_engine(app_config.ENGINE_ROOT, state._c_path_obj), 'absolute_path': str(state._c_path_obj)})
