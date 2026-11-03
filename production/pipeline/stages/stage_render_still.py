# -*- coding: utf-8 -*-
"""Still-image render for a text quote."""
from __future__ import annotations
from shared.channel_flags import sole_page

from production.pipeline.channel_flags import enabled as _channel_enabled

from production.pipeline.stages.state import VariantState
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


def _stage_render_still_01(state: VariantState) -> None:
    """Variant worker stage. Original lines 671-694."""
    state._ratio = (state.page_aspect_ratio or app_config.GEMINI_IMAGE_ASPECT_RATIO or '3:4').strip()
    state._w, state._h = (1080, 1350)
    try:
        state._parts = [int(p) for p in state._ratio.replace(':', '/').split('/')]
        if len(state._parts) == 2 and state._parts[0] > 0 and (state._parts[1] > 0):
            state._w = 1080
            state._h = int(1080 * state._parts[1] / state._parts[0])
    except Exception:
        pass
    try:
        state.img_path_display = _brand_text_quote_bg((state._w, state._h), page_id=state.page_ctx.page_id if state.page_ctx else None, output_path=state.subject_assets / f'{state.stem}_tq_bg.png')
        logging.info('Variant %s | TEXT_QUOTE_BG_OK | path=%s', state.variant + 1, state.img_path_display)
    except Exception as exc:
        state.exc = exc
        _LOG.error('TEXT_QUOTE background generation failed variant %s: %s', state.variant + 1, state.exc, exc_info=True)
        raise

def _stage_render_still_02(state: VariantState) -> None:
    """Variant worker stage. Original lines 697-1080."""
    state.architect = VisualArchitect(channel=ChannelFactory.from_env())
    state.image_prompt = state.architect.build_prompt(state.resolved_subject, variation_index=state.variation_index, total_variants=state.qty, avatar_mode=state.image_avatar_mode, atmosphere_style=state.effective_atmosphere, aspect_ratio=state.page_aspect_ratio or None, style=state.image_style)
    if state.page_ctx and _channel_enabled(state.page_ctx, 'GRAPHITE_STYLE_LOCK') and state.page_ctx.use_style_reference:
        state._wf_il = state.page_ctx.illustration_style.rstrip(' .') if state.page_ctx.illustration_style else 'Highly detailed pencil sketch style illustration, but restricted to only 3 colors: dark azure blue, black, and white. Dramatic lighting, expressive. Clean, minimalist, no messy details.'
        state._wf_ch = state.page_ctx.style_characters.rstrip(' .') if state.page_ctx.style_characters else 'A realistic man and a woman in an intense, dramatic relationship dynamic. They are the consistent recurring personas.'
        state._scene_desc = (state.visual_subject or state.resolved_subject).strip(' .')
        state._SCENE_JUNK = ['bookshelf', 'bookshelves', 'bookcase', 'shelf', 'open book', 'open books', 'coffee cup', 'cup of coffee', 'coffee table', 'armchair', 'armchairs', 'leather chair', 'desk', 'office desk', 'wooden desk', 'window', 'windowsill', 'window seat', 'library', 'reading room', 'sofa', 'couch', 'lamp', 'notebook', 'journal', 'pen', 'pencil on paper', 'table', 'kitchen', 'bedroom', 'living room', 'curtain', 'curtains']
        import re as _re_scene
        state._re_scene = _re_scene
        state._sd = state._scene_desc
        for _jt in state._SCENE_JUNK:
            state._jt = _jt
            state._lo = state._sd.lower()
            state._ix = state._lo.find(state._jt.lower())
            while state._ix != -1:
                state._sd = state._sd[:state._ix] + state._sd[state._ix + len(state._jt):]
                state._lo = state._sd.lower()
                state._ix = state._lo.find(state._jt.lower())
        state._scene_desc = state._re_scene.sub('[ \\t]{2,}', ' ', state._sd).strip(' ,.')
        state.image_prompt = f'{state._wf_il}. {state._wf_ch}. Scene Concept: {state._scene_desc}. CRITICAL COMPLIANCE: Do NOT draw any objects from the text literal. Absolutely NO bookshelves, NO open books, NO coffee cups, NO desks, NO armchairs, NO windows. The frame must only feature the expressive dark azure pencil art couple. Full-bleed, full-screen canvas.'
        _LOG.info('%s IMAGE PROMPT LOCK | hard-override applied | scene: %s', state.page_ctx.page_id if state.page_ctx else sole_page('GRAPHITE_STYLE_LOCK'), state._scene_desc[:80])
    if state._isolated_channel:
        _LOG.info('%s IMAGE PROMPT | adapter compose_image_prompt: %s', state.page_ctx.page_id if state.page_ctx else 'isolated', state.image_prompt[:180])
    elif state._mm_image_prompt_override:
        state.image_prompt = state._mm_image_prompt_override
        _LOG.info('%s IMAGE PROMPT LOCK | hard-override applied: %s', state.page_ctx.page_id if state.page_ctx else sole_page('MEI_PIPELINE'), state.image_prompt[:180])
    if state.post_type in ('LONG_CAPTION_IMAGE', 'CAROUSEL') and (not state._mm_image_prompt_override):
        from production.providers.image.scene_prompt_generator import apply_topic_visual_lock as _tvl
        state._tvl = _tvl
        state._topic_lock = state._tvl(state.image_prompt, topic=state.resolved_subject or '', caption=f'{state.visual_subject or ''} {state.caption or state.overlay_text or ''}', style=state.effective_atmosphere or '')
        state.image_prompt = str(state._topic_lock.get('image_generation_prompt') or state.image_prompt)
        _LOG.info('%s TOPIC LOCK | domain=%s banned=%s | prompt=%s', state.post_type, state._topic_lock.get('domain_id') or '(none)', state._topic_lock.get('banned_subjects'), state.image_prompt[:160])
        print(f'[TOPIC-LOCK] {state.post_type} domain={state._topic_lock.get('domain_id') or '(none)'} banned={state._topic_lock.get('banned_subjects')} prompt={state.image_prompt[:180]}', flush=True)
    state.img_model_id = None
    state._page_cost = state.page_ctx.cost_tier if state.page_ctx is not None else None
    state._override = None
    if state.page_ctx is not None and state.page_ctx.image_model_override:
        state._override = state.page_ctx.image_model_override
    try:
        from production.providers.llm.model_api_flows import get_active_flow as _get_flow, is_flow_explicitly_set as _flow_set, resolved_together_image_model as _flow_img_model
        state._get_flow = _get_flow
        state._flow_set = _flow_set
        state._flow_img_model = _flow_img_model
        import config as _img_cfg
        state._img_cfg = _img_cfg
        state._flow = state._get_flow()
        state._cli_primary = bool(state.page_ctx and state.page_ctx.page_cfg.get('IMAGE_PRIMARY_CLI'))
        state._cli_img_prod = bool(state.page_ctx and state.page_ctx.page_cfg.get('IMG_PRODUCTION_CLI'))
        if state._flow_set() and state._flow is not None and (not state._cli_primary):
            state._prov = getattr(state._flow.image, 'provider', '') or ''
            if state._prov == 'gemini' and state._flow.image.model:
                state._override = state._flow.image.model
            elif state._cli_img_prod and state._prov in ('together', 'deepinfra'):
                state._flow_model = state._flow_img_model()
                if state._flow_model:
                    state._override = state._flow_model
            elif not state._img_cfg.is_gemini_image_model(state._override):
                state._flow_model = state._flow_img_model()
                if state._flow_model:
                    state._override = state._flow_model
    except Exception:
        pass
    from production.providers.llm.model_router import image_model as _route_image
    state._route_image = _route_image
    state._img_route = state._route_image(task='image', page_cost_tier=state._page_cost, model_override=state._override, preferred=app_config.GEMINI_ECONOMIC_IMAGE_MODEL if state.economic else app_config.GEMINI_IMAGE_MODEL, use_premium=True if (state._page_cost or '').lower() == 'premium' else None, log=True)
    state.img_model_id = app_config.normalize_image_model_id(state._img_route.model_id)
    if state.economic or state._img_route.tier == 'cheap':
        _LOG.info('COST-FIRST LOCK | variant=%s | tier=%s | image_model=%s | text_brain=%s', state.variant + 1, state._img_route.tier, state.img_model_id, app_config.GEMINI_ECONOMIC_BRAIN_MODEL)
    import os as _os
    state._os = _os
    from shared.paths import WINDOWS_FACTORY_ROOT as _WINDOWS_FACTORY_ROOT
    state._WINDOWS_FACTORY_ROOT = _WINDOWS_FACTORY_ROOT
    state._wf_style_name = 'Screenshot 2026-06-01 183244.png'
    state._wf_style_env = (state._os.getenv('WONDER_FEED_STYLE_REFERENCE') or '').strip().strip('"').strip("'")
    state._wf_style_repo = REPO_ROOT / 'channels_config' / sole_page("GRAPHITE_STYLE_LOCK") / 'style_reference' / state._wf_style_name
    state._wf_style_windows = state._WINDOWS_FACTORY_ROOT / 'channels_config' / sole_page("GRAPHITE_STYLE_LOCK") / 'style_reference' / state._wf_style_name
    if state._wf_style_env:
        state._WF_STYLE_REF = Path(state._wf_style_env).expanduser()
    elif state._wf_style_windows.is_file():
        state._WF_STYLE_REF = state._wf_style_windows
    else:
        state._WF_STYLE_REF = state._wf_style_repo
    state._WF_STYLE_REF_STR = str(state._WF_STYLE_REF)
    state._style_ref_path = None
    state._style_ref_paths = []
    state._style_ref_weight = 0.72
    if state.page_ctx and _channel_enabled(state.page_ctx, 'GRAPHITE_STYLE_LOCK'):
        print(f'[DEBUG_STYLE_ENGINE] Target image reference assigned: {state._WF_STYLE_REF_STR}')
        if state._os.path.exists(state._WF_STYLE_REF_STR):
            state._style_ref_path = state._WF_STYLE_REF
            state._style_ref_paths = [state._WF_STYLE_REF]
            print('[DEBUG_STYLE_ENGINE] Verification PASSED — style reference loaded.')
            logging.info('%s STYLE REF LOCK | pinned → %s', state.page_ctx.page_id, state._WF_STYLE_REF.name)
        else:
            print(f'[ERROR_STYLE_ENGINE] Verification Failed. Path missing: {state._WF_STYLE_REF_STR}')
            logging.warning('%s style reference not found at expected path: %s', state.page_ctx.page_id, state._WF_STYLE_REF)
    elif state.page_ctx:
        state._style_ref_paths = state.page_ctx.resolve_style_reference_images()
        state._style_ref_weight = state.page_ctx.style_reference_weight
        if state._style_ref_paths:
            state._style_ref_path = state._style_ref_paths[0]
    try:
        import os as _img_os
        state._img_os = _img_os
        print('\n' + '=' * 60)
        print('[DEBUG] IMAGE PIPELINE INITIALIZATION')
        print(f'[DEBUG] Model            : {state.img_model_id or state.bm.image_primary_id}')
        if state.page_ctx and _channel_enabled(state.page_ctx, 'GRAPHITE_STYLE_LOCK'):
            state._dbg_ref = state._WF_STYLE_REF_STR
            print(f'[DEBUG] Style Ref Path   : {state._dbg_ref}')
            print(f'[DEBUG] Ref File Exists  : {state._img_os.path.exists(state._dbg_ref)}')
        else:
            print(f'[DEBUG] Style Ref Path   : {state._style_ref_path or '(none — text-only prompt)'}')
        print(f'[DEBUG] Compiled prompt  : {state.effective_atmosphere[:200]}')
        print('=' * 60 + '\n')
        state.adapter = get_image_adapter(model_id=state.img_model_id, page_cost_tier=state._page_cost, tier=state._img_route.tier)
        if state.page_ctx is not None:
            from production.providers.image.free.integrate import wrap_image_generate
            state.wrap_image_generate = wrap_image_generate
            state._free_producer = 'reel_image' if state.post_type in ('ECONOMIC_REEL', 'WAN_REEL') else 'facebook_card'
            state.adapter.generate = state.wrap_image_generate(state.adapter.generate, channel=state.page_ctx.page_id, page_cfg=state.page_ctx.page_cfg, query=state.resolved_subject or state.visual_subject or '', producer=state._free_producer)
        state._gen_ref = state.effective_ref_path if state.image_avatar_mode == 'ON' else None
        state._gen_weight = None
        if state.page_ctx and _channel_enabled(state.page_ctx, 'MEI_PIPELINE') and (state.image_avatar_mode == 'ON'):
            state._forced = state.page_ctx.forced_avatar_reference_path
            if state._forced is not None and state._forced.is_file():
                state._gen_ref = state._forced
            state._gen_weight = state.page_ctx.avatar_image_weight
        state._act1_role = None
        state._act1_neg = None
        if state.page_ctx and _channel_enabled(state.page_ctx, 'MEI_PIPELINE'):
            from production.media.visual_roles import ROLE_A_NEGATIVE, ROLE_MASTER
            state.ROLE_A_NEGATIVE = ROLE_A_NEGATIVE
            state.ROLE_MASTER = ROLE_MASTER
            state._act1_role = state.ROLE_MASTER
            state._act1_neg = state.ROLE_A_NEGATIVE
        if state.post_type in _SEQUENCE_VIDEO_POST_TYPES:
            state._cover_hit = _find_cached_sequence_still(1, work_dir=state.subject_assets, stem=state.stem)
            if state._cover_hit is not None:
                state.img_path_display = state._cover_hit
                state._reused_cover = True
                _LOG.info('IMAGE CACHE HIT | skipping cover generate — reusing %s', state._cover_hit)
        if state.page_ctx and state.page_ctx.enable_sequence_reel and (state.post_type in _SEQUENCE_VIDEO_POST_TYPES) and (not state._reused_cover):
            _LOG.info('AUDIO-FIRST | deferring stills until TTS word timestamps are ready')
            state.img_path_display = None
            state._reused_cover = True
        if not state._reused_cover:
            state._still_neg = state._act1_neg
            if state._topic_lock and state._topic_lock.get('negative_prompt'):
                state._still_neg = f'{state._act1_neg}, {state._topic_lock['negative_prompt']}' if state._act1_neg else str(state._topic_lock['negative_prompt'])
            state._gen_kwargs = {'reference_image_path': state._gen_ref, 'style_reference_path': state._style_ref_path, 'style_reference_paths': state._style_ref_paths or None, 'style_reference_weight': state._style_ref_weight, 'output_directory': state.subject_assets, 'avatar_mode': state.image_avatar_mode, 'reference_image_weight': state._gen_weight, 'visual_role': state._act1_role, 'negative_prompt': state._still_neg}
            if state.post_type in ('LONG_CAPTION_IMAGE', 'CAROUSEL'):
                from production.qa.reel_visual_qa import generate_and_gate as _still_gate
                state._still_gate = _still_gate
                state.img_path_display, state._topic_gate_extra = state._still_gate(state.adapter.generate, prompt=state.image_prompt, chunk_text=state.resolved_subject or state.visual_subject or '', output_stem=state.stem, output_directory=state.subject_assets, channel=state.page_ctx.page_id if state.page_ctx else sole_page("DOCUMENTARY_PIPELINE"), search_dirs=[state.subject_assets] if state.subject_assets else [], generate_kwargs=state._gen_kwargs, banned_subjects=list(state._topic_lock.get('banned_subjects') or []) if state._topic_lock else None, domain_anchors=str((state._topic_lock or {}).get('domain_anchors') or ''), prefer_stem=state.stem)
            else:
                state.img_path_display = state.adapter.generate(state.image_prompt, output_stem=state.stem, **state._gen_kwargs)
        state.img_used = state.adapter.last_gemini_image_model_used or state.bm.image_primary_id
        logging.info('Variant %s | IMAGE_OK | model_used=%s | path=%s', state.variant + 1, state.img_used, state.img_path_display)
    except Exception as exc:
        state.exc = exc
        state.failed_mid = state.img_model_id or state.bm.image_primary_id
        if state.adapter is not None:
            state.failed_mid = state.adapter.last_gemini_image_failure_model_id or state.failed_mid
        _LOG.error('Image generation failed variant %s attempted_model=`%s`', state.variant + 1, state.failed_mid, exc_info=True)
        logging.error('VARIANT_FAIL | GeminiImageAdapter | variant=%s | model=`%s` | err=%s', state.variant + 1, state.failed_mid, state.exc)
        raise
    if isinstance(state.img_path_display, Path) and state.img_path_display.is_file() and (state.post_type not in ('ECONOMIC_REEL', 'WAN_REEL')):
        state.video_path_str = _maybe_convert_to_video(state.img_path_display, duration=7, page_ctx=state.page_ctx)

def _stage_render_still_03(state: VariantState) -> None:
    """Variant worker stage. Original lines 696-1080."""
    if not state.skip_image:
        _stage_render_still_02(state)
