# -*- coding: utf-8 -*-
"""Scene export, reel compile, and the QA seal. unique_stills runs before export."""
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


def _stage_compile_reel_01(state: VariantState) -> None:
    """Variant worker stage. Original lines 4093-4470."""
    state._logo_img_path = state.logo_path or (state.page_ctx.logo_png if state.page_ctx and state.page_ctx.logo_exists else None)
    state._brand_label = f'@ {state.page_ctx.display_name}' if state.page_ctx and state.page_ctx.display_name and (not state._logo_img_path) else None
    state._use_sequence = state.page_ctx is not None and state.page_ctx.enable_sequence_reel and (len(state._sequence_image_paths) >= 2)
    if state._use_sequence:
        _LOG.info('SEQUENCE_REEL | %d images → %s', len(state._sequence_image_paths), state._reel_target.name)
        state._wpp = state.page_ctx.subtitle_words_per_phrase if state.page_ctx else 4
        state._sub_fill = state.page_ctx.subtitle_fill if state.page_ctx else (255, 230, 0)
        state._sub_sw = state.page_ctx.subtitle_stroke_width if state.page_ctx else 0
        state._sub_sf = state.page_ctx.subtitle_stroke_fill if state.page_ctx else None
        state._voice_gain = float(state.page_ctx.voice_volume_gain) if state.page_ctx and state.page_ctx.voice_volume_gain is not None else 1.15 if state.page_ctx and (state.page_ctx.page_id or '').lower() == 'master_mei' else 1.0
        state._amb_mul = state.page_ctx.ambient_sfx_gain_mul if state.page_ctx and state.page_ctx.ambient_sfx_gain_mul is not None else 1.0
        state._paced_act_durs = None
        if state.page_ctx and (not state._is_mei) and state._sequence_image_paths:
            state._timeline_s = float(state._reel_dur or state.page_ctx.reel_duration or 80.0)
            if state._voice_path is not None:
                try:
                    from agents.media.audio_engine import _audio_file_duration_s as _voice_dur_s
                    state._voice_dur_s = _voice_dur_s
                    state._vd = float(state._voice_dur_s(Path(state._voice_path)))
                    if state._vd > 1.0:
                        state._timeline_s = state._vd + 1.0
                except Exception:
                    pass
            state._n_imgs = len(state._sequence_image_paths)
            state._plan_ref = getattr(state, '_planned_scene_durs', None)
            if state._plan_ref and len(state._plan_ref) == state._n_imgs:
                state._paced_act_durs = _scale_scene_durations(state._plan_ref, state._timeline_s)
                _LOG.info('ECONOMIC_REEL plan_scenes act_durs | %s → scaled %s', _describe_scene_plan(state._plan_ref), _describe_scene_plan(state._paced_act_durs))
            elif state.page_ctx.uses_plan_scenes_pacing:
                if state._plan_ref:
                    state._base = list(state._plan_ref)
                    if len(state._base) > state._n_imgs:
                        state._base = state._base[:state._n_imgs]
                    else:
                        state._pad = state._base[-1] if state._base else 5.0
                        state._base = state._base + [state._pad] * (state._n_imgs - len(state._base))
                    state._paced_act_durs = _scale_scene_durations(state._base, state._timeline_s)
                else:
                    state._paced_act_durs = _plan_scenes(state._timeline_s, state.page_ctx.scene_duration, progressive_start_s=state.page_ctx.scene_progressive_start_s, progressive_step_every=state.page_ctx.scene_progressive_step_every, progressive_step_s=state.page_ctx.scene_progressive_step_s, progressive_cap_s=state.page_ctx.scene_progressive_cap_s, min_scenes=state._n_imgs, max_scenes=state._n_imgs)
                _LOG.info('ECONOMIC_REEL plan_scenes (aligned n=%d) | %s', state._n_imgs, _describe_scene_plan(state._paced_act_durs))
            elif state.page_ctx.reel_use_hook_body_pacing:
                state._paced_act_durs = _build_hook_body_act_durations(state._n_imgs, state._timeline_s, hook_hold_s=state.page_ctx.reel_hook_hold_s, body_hold_s=state.page_ctx.reel_body_hold_s)
                _LOG.info('ECONOMIC_REEL legacy hook/body act_durs | %s', _describe_scene_plan(state._paced_act_durs))
        state._compile_act_durs = list(getattr(state, '_ak_act_durs', None) or []) if state.page_ctx and str(getattr(state.page_ctx, 'page_id', '')).lower() == 'ancient_knowledge' and getattr(state, '_ak_act_durs', None) else state._paced_act_durs
        state._act_video_paths = None
        if state._is_wan_reel:
            from core.wan_reel_production import generate_wan_act_videos as _gen_wan_acts
            state._gen_wan_acts = _gen_wan_acts
            state._wan_dir = (state._episode_assets_dir or Path(app_config.ASSETS_DIR)) / 'wan_clips'
            state._holds = [float(d) for d in state._compile_act_durs or []]
            state._n_imgs = len(state._sequence_image_paths)
            if not state._holds:
                state._holds = [float(state._reel_dur) / max(1, state._n_imgs)] * state._n_imgs
            if len(state._holds) > state._n_imgs:
                state._holds = state._holds[:state._n_imgs]
            elif len(state._holds) < state._n_imgs:
                state._pad = state._holds[-1] if state._holds else 5.0
                state._holds = state._holds + [state._pad] * (state._n_imgs - len(state._holds))
            state._wan_t0 = time.monotonic()
            _LOG.info('WAN_REEL | parallel Wan img2vid | n=%d holds=%s', state._n_imgs, ','.join((f'{h:.2f}' for h in state._holds)))
            print(f'\n[WAN_REEL] Generating {state._n_imgs} Wan clips in parallel (cap=REMOTE_GPU_MAX_PARALLEL)…')
            state._act_video_paths, state._wan_act_status = state._gen_wan_acts([Path(p) for p in state._sequence_image_paths], list(state._spoken_snippets or []), state._holds, topic=str(state.resolved_subject or ''), output_dir=state._wan_dir, stem=state.stem, cost_tracker=state.cost_tracker)
            state._wan_stage_times['wan_video_s'] = round(time.monotonic() - state._wan_t0, 2)
            state._n_wan = sum((1 for s in state._wan_act_status if s == 'wan'))
            state._n_fb = len(state._wan_act_status) - state._n_wan
            print(f'[WAN_REEL] Video gen {state._wan_stage_times['wan_video_s']:.1f}s | wan={state._n_wan} ken_burns_fallback={state._n_fb}')
            _LOG.info('WAN_REEL video gen | wall=%.1fs | wan=%d fallback=%d', state._wan_stage_times['wan_video_s'], state._n_wan, state._n_fb)
            state._compile_t0 = time.monotonic()
        if state.page_ctx and (state.page_ctx.page_id or '').lower() == 'ancient_knowledge':
            assert_voice_and_subtitles_ready(voice_audio=state._voice_path, word_timings=state._word_timings, page_id='ancient_knowledge')
        state.reel_path = _core_compile_sequence_reel(state._sequence_image_paths, state.overlay_text, voice_audio=state._voice_path, ambient_audio=state._ambient_path, output_path=state._reel_target, target_duration=state._reel_dur, act_duration_s=state.page_ctx.reel_act_duration if state.page_ctx else None, act_durations=state._compile_act_durs, strict_act_durations=bool(state.page_ctx and str(getattr(state.page_ctx, 'page_id', '')).lower() == 'ancient_knowledge' and getattr(state, '_ak_act_durs', None)), word_timings=state._word_timings or None, font_path=state._font_path_abs or None, overlay_opacity=state.page_ctx.reel_overlay_opacity if state.page_ctx else 0.35, enable_hook_text=state.page_ctx.enable_top_hook_text if state.page_ctx else True, vignette_strength=state.page_ctx.vignette_strength if state.page_ctx else 0.0, grain_intensity=state.page_ctx.grain_intensity if state.page_ctx else 18.0, logo_image_path=state._logo_img_path, logo_width_px=state.page_ctx.logo_width_px if state.page_ctx else 200, logo_y_offset_px=state.page_ctx.logo_y_offset_px if state.page_ctx else 100, logo_opacity=state.page_ctx.logo_opacity if state.page_ctx else 0.85, logo_max_height_px=state.page_ctx.logo_max_height_px if state.page_ctx else None, subtitle_fontsize=state.page_ctx.subtitle_fontsize if state.page_ctx else 56, subtitle_y_position=state.page_ctx.subtitle_y_position if state.page_ctx else None, hook_y_frac=state.page_ctx.hook_y_frac if state.page_ctx else 0.5, page_id=state.page_ctx.page_id if state.page_ctx else '', words_per_phrase=state._wpp, subtitle_fill=state._sub_fill, subtitle_stroke_width=state._sub_sw, subtitle_stroke_fill=state._sub_sf, enable_flicker=state.page_ctx.enable_flicker if state.page_ctx else False, enable_light_rays=state.page_ctx.enable_light_rays if state.page_ctx else False, enable_dust_particles=state.page_ctx.enable_dust_particles if state.page_ctx else False, enable_light_refraction=state.page_ctx.enable_light_refraction if state.page_ctx else False, cta_text=_re.sub('\\[(?:cackles|chuckles|dry\\s*laugh)\\]\\s*', '', getattr(state, '_cta_text', '') or '', flags=_re.IGNORECASE).strip(), cta_start_s=float(getattr(state, '_narr_dur', -1.0)) + 0.3 if float(getattr(state, '_narr_dur', -1.0) or -1.0) > 0.5 else -1.0, cta_y_position=state.page_ctx.cta_subtitle_y_position if state.page_ctx else None, narration_duration_s=float(getattr(state, '_narr_dur', -1.0)) if float(getattr(state, '_narr_dur', -1.0) or -1.0) > 0.5 else None, cta_visual_gap_s=0.3, ambient_volume=state.page_ctx.ambient_volume if state.page_ctx else None, sfx_loop_audio=state._sfx_loop_path, sfx_loop_volume=state.page_ctx.atmosphere_sfx_volume if state.page_ctx else 0.35, sfx_fade_in_s=state.page_ctx.atmosphere_sfx_fade_in if state.page_ctx else 0.2, impact_sfx_audio=None if state._use_music_bed else state._impact_sfx_path, impact_sfx_volume=0.0 if state._use_music_bed else None, hook_max_s=float((getattr(state.page_ctx, 'page_cfg', None) or {}).get('REEL_HOOK_MAX_S', 8.0)) if state.page_ctx and state._is_mei else 0.0, voice_volume_gain=1.0 if state._is_mei else state._voice_gain, ambient_gain_mul=state._amb_mul, ambient_duck_ratio=state.page_ctx.ambient_duck_ratio if state.page_ctx else None, ambient_duck_until_s=getattr(state, '_narr_dur', None), master_audio_gain=state.page_ctx.master_audio_gain if state.page_ctx and state._use_music_bed else None, ambient_profile=state.page_ctx.ambient_music_style if state.page_ctx else 'warrior' if state._is_mei else 'mystery', bgm_start_s=state.page_ctx.bgm_start_time if state.page_ctx else 0.0, bgm_fade_in_s=state.page_ctx.bgm_fade_in_duration if state.page_ctx else 0.0, sfx_volume_gain_db=state.page_ctx.sfx_volume_gain_db if state.page_ctx else 0.0, tail_pad_s=state.page_ctx.reel_tail_pad_s if state.page_ctx else 1.0, force_exact_duration=state.page_ctx.reel_force_exact_duration if state.page_ctx else False, exact_duration_s=state.page_ctx.reel_duration if state.page_ctx else 80.0, ffmpeg_preset=state.page_ctx.encoding_preset if state.page_ctx else 'medium', pacing_sequence=state.page_ctx.pacing_sequence if state.page_ctx else None, scene_length=state.page_ctx.scene_length if state.page_ctx else None, target_duration_min=state.page_ctx.reel_duration_target_min if state.page_ctx else None, target_duration_max=state.page_ctx.reel_duration_target_max if state.page_ctx else None, enable_subtitle_padding=state.page_ctx.enable_subtitle_padding if state.page_ctx else True, act_video_paths=state._act_video_paths)
        if state._is_wan_reel and hasattr(state, '_compile_t0'):
            state._wan_stage_times['compile_s'] = round(time.monotonic() - float(state._compile_t0), 2)
    else:
        state.reel_path = compile_dynamic_reel(Path(state.img_path_display), state.overlay_text, voice_audio=state._voice_path, ambient_audio=state._ambient_path, output_path=state._reel_target, target_duration=state._reel_dur, font_path=state._font_path_abs or None, font_size_scale=state._font_size_scale, overlay_opacity=state.page_ctx.reel_overlay_opacity if state.page_ctx else 0.35, word_timings=state._word_timings or None, brand_label=state._brand_label, logo_image_path=state._logo_img_path, subtitle_fontsize=state.page_ctx.subtitle_fontsize if state.page_ctx else 46, subtitle_y_position=state.page_ctx.subtitle_y_position if state.page_ctx else None, logo_width_px=state.page_ctx.logo_width_px if state.page_ctx else 160, logo_y_offset_px=state.page_ctx.logo_y_offset_px if state.page_ctx else 90, logo_opacity=state.page_ctx.logo_opacity if state.page_ctx else 0.7, logo_max_height_px=state.page_ctx.logo_max_height_px if state.page_ctx else None, hook_y_frac=state.page_ctx.hook_y_frac if state.page_ctx else 0.55, page_id=state.page_ctx.page_id if state.page_ctx else '', sub_text=None)
    _LOG.info('%s compiled → %s', 'WAN_REEL' if state._is_wan_reel else 'ECONOMIC_REEL', state.reel_path.name)
    state.video_path_str = str(state.reel_path)
    print(f'[reel] Video compiled -> {state.reel_path}')
    try:
        from modules.asset_library import register_asset
        state.register_asset = register_asset
        state._lib_channel = (state.page_ctx.page_id if state.page_ctx else app_config.ACTIVE_PAGE) or 'ancient_knowledge'
        state._lib_caption = state.caption if state.caption and state.caption != '(skipped)' else getattr(state, '_early_seq_script', None) or state.overlay_text or ''
        state._lib_audio = float(getattr(state, '_ak_total_audio', None) or getattr(state, '_narr_dur', None) or 0.0)
        state._lib_meta = getattr(state, '_sr_act_meta', None) or {}
        state._lib_video = str(state.reel_path)
        for _si, _still in enumerate(state._sequence_image_paths or []):
            state._si = _si
            state._still = _still
            state._meta = state._lib_meta.get(state._si) or {}
            state.register_asset(channel=str(state._lib_channel), post_type=str(state.post_type or 'ECONOMIC_REEL'), local_path=str(state._still), prompt=str(state._meta.get('prompt') or ''), caption=str(state._lib_caption), video_path=state._lib_video if state._si == 0 else '', audio_duration_s=state._lib_audio if state._lib_audio > 0 else None, asset_kind='still', platform='facebook')
        state.register_asset(channel=str(state._lib_channel), post_type=str(state.post_type or 'ECONOMIC_REEL'), local_path=state._lib_video, prompt='', caption=str(state._lib_caption), video_path=state._lib_video, audio_duration_s=state._lib_audio if state._lib_audio > 0 else None, asset_kind='video', platform='facebook')
    except Exception as _lib_exc:
        state._lib_exc = _lib_exc
        _LOG.warning('asset_library reel register skipped (%s)', state._lib_exc)

def _stage_compile_reel_02(state: VariantState) -> None:
    """Variant worker stage. Original lines 4471-4551."""
    if state._is_wan_reel and state._wan_stage_times:
        state._fb = [s for s in state._wan_act_status if s != 'wan']
        print('[WAN_REEL] Stages | ' + ' | '.join((f'{k}={v:.1f}s' for k, v in state._wan_stage_times.items())))
        if state._wan_act_status:
            state._n_wan = sum((1 for s in state._wan_act_status if s == 'wan'))
            print(f'[WAN_REEL] Acts | wan={state._n_wan}/{len(state._wan_act_status)} ken_burns_fallback={len(state._wan_act_status) - state._n_wan}')
            for _i, _st in enumerate(state._wan_act_status, 1):
                state._i = _i
                state._st = _st
                if state._st != 'wan':
                    print(f'  act {state._i}: {state._st}')
    state._api_imgs = max(0, int(state._images_generated_this_variant or 0))
    state._batch_so_far = int(state._api_imgs)
    state._lock = state.batch_image_lock or state.write_lock
    if state.batch_image_counter is not None:
        with state._lock:
            state.batch_image_counter[0] = int(state.batch_image_counter[0]) + int(state._api_imgs)
            state._batch_so_far = int(state.batch_image_counter[0])
    state._cats = state.cost_tracker.category_totals() if state.cost_tracker is not None else {}
    state._reel_cost = float(state._cats.get('total') or 0.0) if state._cats else 0.003 * state._api_imgs
    state._batch_usd = state._reel_cost
    if state.batch_cost_tracker is not None:
        state._batch_cats = state.batch_cost_tracker.category_totals()
        state._batch_usd = float(state._batch_cats.get('total') or 0.0) + state._reel_cost
    state._reel_stills = len([_still for _still in state._sequence_image_paths or [] if _still])
    _print_video_cost_summary(state.cost_tracker, image_count=state._api_imgs, reel_stills=state._reel_stills, image_model_label='FLUX Schnell', variant_index=state.variant + 1, total_variants=state.qty, batch_images_so_far=state._batch_so_far, reel_cost_usd=state._reel_cost, batch_cost_usd=state._batch_usd, post_type=state.post_type, audio_label='F5TTS' if _reel_audio_is_f5() else 'ElevenLabs')
    state._b2_video_url = ''
    try:
        state._b2 = B2VideoUploader()
        state._b2_video_url = state._b2.upload(state.reel_path)
        _LOG.info('B2 upload OK → %s', state._b2_video_url)
        print(f'[B2] Public URL: {state._b2_video_url}')
    except B2StorageCapError as _b2_cap:
        state._b2_cap = _b2_cap
        _LOG.warning('%s', state._b2_cap)
        state._b2_video_url = ''
    except Exception as _b2_exc:
        state._b2_exc = _b2_exc
        state._err_l = str(state._b2_exc).lower()
        if 'accessdenied' in state._err_l or 'access denied' in state._err_l:
            _LOG.warning('[B2] Storage cap / AccessDenied for %s — using local/ImgBB.', state.reel_path.name)
        else:
            _LOG.warning('B2 upload failed for %s (%s: %s) — postplanner will use local path.', state.reel_path.name, type(state._b2_exc).__name__, state._b2_exc)
        state._b2_video_url = ''

def _stage_compile_reel_03(state: VariantState) -> None:
    """Variant worker stage. Original lines 4553-4561."""
    _LOG.error('ECONOMIC_REEL video compilation failed (variant %s): %s', state.variant + 1, state.reel_exc, exc_info=True)
    print(f'[reel] COMPILE ERROR (variant {state.variant + 1}): {type(state.reel_exc).__name__}: {state.reel_exc}')
    state.reel_path = None

def _stage_compile_reel_04(state: VariantState) -> None:
    """Variant worker stage. Original lines 3561-3936."""
    _LOG.info('ECONOMIC_REEL | Launching video compilation pipeline (variant %s)…', state.variant + 1)
    if not getattr(state, '_ak_voice_ready', None):
        state._voice_path = None
    state._ambient_path = None
    state._reel_dir = Path(app_config.PAGE_OUTPUTS_DIR) / 'clips'
    os.makedirs(state._reel_dir, exist_ok=True)
    state._real_caption = state.caption if state.caption and state.caption != '(skipped)' else ''
    state._voiceover_script = state._real_caption or state.overlay_text
    if state.page_ctx is not None and state.page_ctx.enable_sequence_reel and (state.caption_engine is not None) and state.resolved_subject:
        try:
            if getattr(state, '_ak_voice_ready', None) and state._early_seq_script:
                state._seq_script = state._early_seq_script
                state._voiceover_script = state._seq_script
                _LOG.info('SEQUENCE_REEL | reusing AK-committed voice script (%d words) — audio already synthesised.', len(state._seq_script.split()))
            elif state._early_seq_script and len(state._early_seq_script.split()) >= 40:
                state._seq_script = state._early_seq_script
                state._voiceover_script = state._seq_script
                _commit_reel_angle(state.caption_engine, state.page_ctx, state.resolved_subject, state._seq_script)
                _LOG.info('SEQUENCE_REEL | reusing early narrative script (%d words) — no regen needed.', len(state._seq_script.split()))
            else:
                _LOG.info("SEQUENCE_REEL | generating long-form voiceover script for '%s' (voice pref: %s)", state.resolved_subject, state.page_ctx.tts_voice_preference or 'default')
                state._words_tgt = state.page_ctx.reel_narration_words
                state._n_for_script = max(2, len(state._sequence_image_paths) if state._sequence_image_paths else state.page_ctx.reel_image_count)
                state._seq_script = state.caption_engine.generate_sequence_voiceover(state.resolved_subject, page_niche=state.page_ctx.content_niche, persona_voice=state.page_ctx.tts_voice_preference or ('calm, deep, highly wise, authoritative ancient master' if (state.page_ctx.page_id or '').lower() == 'master_mei' else 'investigative, neutral, immersive'), n_acts=state._n_for_script, duration_s=state.page_ctx.reel_duration, total_words_target=state._words_tgt, economic=state.economic, niche_disclaimer=state.page_ctx.niche_disclaimer, cta_line='', narrative_mode=state.page_ctx.narrative_mode, batch_angle_block=state._batch_angle_block, previously_generated_hooks=list(state.generated_hooks_cache or []))
                if state.cost_tracker is not None and state._seq_script:
                    state.cost_tracker.track_text(char_count=len(state._seq_script))
                if state._seq_script and len(state._seq_script.split()) >= 40:
                    state._voiceover_script = state._seq_script
                    _commit_reel_angle(state.caption_engine, state.page_ctx, state.resolved_subject, state._seq_script)
                    _LOG.info('SEQUENCE_REEL | fallback voiceover generated: %d words', len(state._seq_script.split()))
        except Exception as _seq_exc:
            state._seq_exc = _seq_exc
            _LOG.warning('SEQUENCE_REEL | voiceover generation failed: %s — using caption.', state._seq_exc)
    state._word_timings = []
    if getattr(state, '_ak_voice_ready', None) and state._voice_path is not None:
        state._word_timings = list(getattr(state, '_narr_word_timings', None) or [])
        state._cta_text = (state.page_ctx.reel_cta_text if state.page_ctx else '') or ''
        if state._cta_text and float(getattr(state, '_narr_dur', None) or 0) > 0:
            state._cta_offset = float(state._narr_dur) + 1.0
            state._cta_words = state._cta_text.split()
            state._cta_slot = 3.5 / max(1, len(state._cta_words))
            state._word_timings = state._word_timings + [(w, state._cta_offset + i * state._cta_slot, state._cta_offset + (i + 1) * state._cta_slot) for i, w in enumerate(state._cta_words)]
        _LOG.info('SEQUENCE_REEL | reusing pre-image TTS | narr=%.1fs | %s', float(getattr(state, '_narr_dur', None) or 0), Path(state._voice_path).name)
    elif state._voiceover_script and app_config.ELEVENLABS_API_KEY:
        state._max_words = (state.page_ctx.reel_narration_words if state.page_ctx else 140) + 30
        if state.page_ctx and (state.page_ctx.page_id or '').lower() in ('master_mei', 'ancient_knowledge'):
            state._max_words = int(state.page_ctx.reel_narration_max_words)
        state._voiceover_script = _trim_script_to_word_limit(state._voiceover_script, max_words=state._max_words)
        state._is_mei_page = (state.page_ctx.page_id if state.page_ctx else '').lower() == 'master_mei'
        state._tts_ssml = bool(state.page_ctx.tts_enable_ssml) if state.page_ctx else False
        if state._is_mei_page:
            from agents.media.mei_narrative import prepare_mei_tts_text, strip_inline_follow_cta
            state.prepare_mei_tts_text = prepare_mei_tts_text
            state.strip_inline_follow_cta = strip_inline_follow_cta
            state._pre = state._voiceover_script
            state._voiceover_script = state.prepare_mei_tts_text(state._voiceover_script)
            state._voiceover_script = state.strip_inline_follow_cta(state._voiceover_script)
            if state._pre != state._voiceover_script:
                _LOG.info('MASTER_MEI TTS prep | POV/blacklist + break pauses (%d → %d chars, breaks=%d)', len(state._pre), len(state._voiceover_script), state._voiceover_script.lower().count('<break'))
            state._tts_ssml = bool(state.page_ctx.tts_enable_ssml) if state.page_ctx else True
        elif state.page_ctx is None or state.page_ctx.strip_audio_tags_before_tts:
            state._pre_strip = state._voiceover_script
            state._voiceover_script = _strip_audio_behavior_tags(state._voiceover_script)
            if state._pre_strip != state._voiceover_script:
                _LOG.info('TTS tag strip | removed bracket tags before ElevenLabs (%d → %d chars)', len(state._pre_strip), len(state._voiceover_script))
        state._narration_out = state._reel_dir / f'{state.stem}_v{state.variant + 1:02d}_narration.mp3'
        state._voice_id = state.page_ctx.elevenlabs_voice_id if state.page_ctx else None
        state._tts_speed = state.page_ctx.tts_narration_speed if state.page_ctx else None
        state._tts_vs = state.page_ctx.elevenlabs_voice_settings if state.page_ctx else None
        state._tts_expressive = state.page_ctx.tts_expressive_mode if state.page_ctx and hasattr(state.page_ctx, 'tts_expressive_mode') else (state.page_ctx.page_id or '').lower() == 'master_mei' if state.page_ctx else False
        state._tts_script = state._voiceover_script
        if (state.page_ctx.page_id if state.page_ctx else '').lower() == 'ancient_knowledge':
            from modules.reel_artefact.speech import spoken_numbers as _spoken_numbers
            state._spoken_numbers = _spoken_numbers
            state._tts_script = state._spoken_numbers(state._voiceover_script)
        try:
            state._voice_path, state._word_timings = generate_voiceover_with_timestamps(state._tts_script, state._narration_out, voice_id=state._voice_id or None, model_id=state.page_ctx.elevenlabs_model if state.page_ctx else 'eleven_v3', speed=state._tts_speed, voice_settings=state._tts_vs or None, enable_ssml=state._tts_ssml, expressive_mode=state._tts_expressive)
            state._word_timings = _filter_audio_tag_timings(state._word_timings)
        except Exception as vaudio_exc:
            state.vaudio_exc = vaudio_exc
            state._ak_must_speak = (state.page_ctx.page_id if state.page_ctx else '').lower() == 'ancient_knowledge'
            if state._ak_must_speak:
                raise RuntimeError(f'Voiceover generation failed (variant {state.variant + 1}): {state.vaudio_exc} — refusing silent ancient_knowledge compile.') from state.vaudio_exc
            _LOG.warning('Voiceover generation failed (variant %s): %s — reel will be silent.', state.variant + 1, state.vaudio_exc)
        state._cta_text = (state.page_ctx.reel_cta_text if state.page_ctx else '') or ''
        from agents.media.mei_narrative import approved_cta_text, fix_cta_typos
        state.approved_cta_text = approved_cta_text
        state.fix_cta_typos = fix_cta_typos
        if (state.page_ctx.page_id if state.page_ctx else '').lower() == 'master_mei':
            state._cta_text = state.approved_cta_text()
        if state.page_ctx is None or state.page_ctx.strip_audio_tags_before_tts:
            state._cta_text = _strip_audio_behavior_tags(state._cta_text)
        state._cta_text = state.fix_cta_typos(state._cta_text or '')
        state._cta_path = None
        state._cta_word_timings = []
        if state._cta_text and state._voice_path is not None:
            state._cta_out = state._reel_dir / f'{state.stem}_v{state.variant + 1:02d}_cta.mp3'
            try:
                state._is_mei_cta = (state.page_ctx.page_id if state.page_ctx else '').lower() == 'master_mei'
                if state._is_mei_cta:
                    from agents.media.audio_engine import default_cta_cache_path, ensure_cached_cta_voiceover
                    state.default_cta_cache_path = default_cta_cache_path
                    state.ensure_cached_cta_voiceover = ensure_cached_cta_voiceover
                    import shutil as _shutil_cta
                    state._shutil_cta = _shutil_cta
                    state._cache = state.ensure_cached_cta_voiceover(state._cta_text, state.default_cta_cache_path(), voice_id=state._voice_id or None, model_id=state.page_ctx.elevenlabs_model if state.page_ctx else 'eleven_v3', speed=state._tts_speed, voice_settings=state._tts_vs or None, expressive_mode=state._tts_expressive)
                    if state._cache is not None and Path(state._cache).is_file():
                        state._shutil_cta.copy2(str(state._cache), str(state._cta_out))
                        state._cta_path = state._cta_out
                        try:
                            from moviepy import AudioFileClip as _AFC_CTA
                            state._AFC_CTA = _AFC_CTA
                            with state._AFC_CTA(str(state._cta_path)) as _cta_afc:
                                state._cta_afc = _cta_afc
                                state._cta_dur_est = float(state._cta_afc.duration or 3.0)
                        except Exception:
                            state._cta_dur_est = 3.0
                        state._cta_words = state._cta_text.split()
                        if state._cta_words:
                            state._slot = state._cta_dur_est / max(1, len(state._cta_words))
                            state._cta_word_timings = [(w, i * state._slot, (i + 1) * state._slot) for i, w in enumerate(state._cta_words)]
                        _LOG.info('CTA cache used | %s (%d chars) — ElevenLabs skip on hit', state._cta_path.name, len(state._cta_text))
                    else:
                        raise RuntimeError('CTA cache unavailable')
                else:
                    state._cta_path, state._cta_word_timings = generate_voiceover_with_timestamps(state._cta_text, state._cta_out, voice_id=state._voice_id or None, model_id=state.page_ctx.elevenlabs_model if state.page_ctx else 'eleven_v3', speed=state._tts_speed, voice_settings=state._tts_vs or None, expressive_mode=state._tts_expressive)
                    state._cta_word_timings = _filter_audio_tag_timings(state._cta_word_timings)
                    _LOG.info('CTA voiceover generated | %s (%d chars)', state._cta_path.name, len(state._cta_text))
            except Exception as _cta_exc:
                state._cta_exc = _cta_exc
                _LOG.warning('CTA voiceover failed (%s) — CTA will be omitted.', state._cta_exc)
        if state._voice_path is not None and state._cta_path is not None:
            state._narr_dur = 0.0
            try:
                from agents.media.audio_engine import _audio_file_duration_s as _audio_dur_s
                state._audio_dur_s = _audio_dur_s
                state._narr_dur = float(state._audio_dur_s(Path(state._voice_path)))
            except Exception:
                state._narr_dur = 0.0
            if state._narr_dur <= 0.05 and state._word_timings:
                state._narr_dur = float(state._word_timings[-1][2])
            if state._narr_dur <= 0.05:
                try:
                    from moviepy import AudioFileClip as _AFC
                    state._AFC = _AFC
                    with state._AFC(str(state._narration_out)) as _afc:
                        state._afc = _afc
                        state._narr_dur = float(state._afc.duration or 0.0)
                except Exception:
                    state._narr_dur = 0.0
            state._stitched_out = state._reel_dir / f'{state.stem}_v{state.variant + 1:02d}_voice.mp3'
            state._voice_path = _stitch_audio_sequential(state._voice_path, state._cta_path, state._stitched_out, silence_s=1.0)
            state._cta_offset = state._narr_dur + 1.0
            if not state._cta_word_timings:
                try:
                    from agents.media.audio_engine import _audio_file_duration_s as _audio_dur_s2, approximate_word_timings as _approx_wt
                    state._audio_dur_s2 = _audio_dur_s2
                    state._approx_wt = _approx_wt
                    state._cta_dur_fill = float(state._audio_dur_s2(Path(state._cta_path)))
                    state._cta_word_timings = state._approx_wt(state._cta_text, state._cta_dur_fill)
                except Exception:
                    state._cta_word_timings = []
            state._word_timings = state._word_timings + [(w, s + state._cta_offset, e + state._cta_offset) for w, s, e in state._cta_word_timings]
            _LOG.info('Subtitle coverage extended | narr=%.1fs + 1.0s + cta=%.1fs words (%d total tokens) | cta_overlay_start=%.1fs', state._narr_dur, state._cta_word_timings[-1][2] if state._cta_word_timings else 0.0, len(state._word_timings), state._cta_offset)
        elif state._voice_path is None:
            state._voice_path = None
        if state._voice_path is not None and state.page_ctx and ((state.page_ctx.page_id or '').lower() == 'master_mei'):
            try:
                state._voice_path = apply_voice_loudnorm(state._voice_path)
            except Exception as _ln_exc:
                state._ln_exc = _ln_exc
                _LOG.debug('VO loudnorm skipped: %s', state._ln_exc)
    else:
        state._skip_why = 'ELEVENLABS_API_KEY not set' if not app_config.ELEVENLABS_API_KEY else 'no narration script'
        if (state.page_ctx.page_id if state.page_ctx else '').lower() == 'ancient_knowledge':
            raise RuntimeError(f'ECONOMIC_REEL | Voiceover skipped — {state._skip_why} (refusing silent ancient_knowledge compile).')
        _LOG.warning('ECONOMIC_REEL | Voiceover skipped — %s', state._skip_why)
    if state.cost_tracker is not None and state._voiceover_script:
        state._already_tts = int(state.cost_tracker.category_totals().get('tts_chars') or 0)
        if state._already_tts <= 0:
            state._cta_chars = len(getattr(state, '_cta_text', ''))
            state.cost_tracker.track_audio(char_count=len(state._voiceover_script) + state._cta_chars, sfx=False)
    if state._early_music_thread is not None:
        state._early_music_thread.join()
    state._early_mode = str(state._early_music_box.get('mode') or '')
    state._impact_sfx_path = None
    state._sfx_loop_path = None
    state._target_dur = state.page_ctx.reel_duration if state.page_ctx else 105.0
    state._is_mei = bool(state.page_ctx and (state.page_ctx.page_id or '').lower() == 'master_mei')
    state._use_music_bed = bool(app_config.ELEVENLABS_API_KEY and state.page_ctx and getattr(state.page_ctx, 'use_music_v2_bed', False))

def _stage_compile_reel_05(state: VariantState) -> None:
    """Variant worker stage. Original lines 3937-4561."""
    if state._use_music_bed:
        state._music_min = 40.0
        try:
            state._cfg = getattr(state.page_ctx, 'page_cfg', None) or {}
            state._music_min = float(state._cfg.get('MUSIC_V2_MIN_SECONDS', 40.0) or 40.0)
        except Exception:
            state._music_min = 40.0
        state._music_style = state.page_ctx.ambient_music_style if state.page_ctx else 'warrior' if state._is_mei else 'mystery'
        state._music_dir = state.page_ctx.music_prompt_directive_path if state.page_ctx else None
        if state._early_mode == 'v2':
            state._music_bed = state._early_music_box.get('bed')
            state._sfx_loop_path = state._early_music_box.get('sfx')
        else:
            state._music_bed, state._impact_sfx_path = generate_master_mei_soundscape(state._reel_dir, stem=f'{state.stem}_v{state.variant + 1:02d}', duration_seconds=max(state._music_min, float(state._target_dur)), include_impact_sfx=False, topic=str(state.resolved_subject or ''), directive_path=state._music_dir, style_profile=state._music_style, channel_name=state.page_ctx.page_id or '' if state.page_ctx else '')
        state._impact_sfx_path = None
        if state._music_bed is not None:
            state._ambient_path = state._music_bed
            _LOG.info('%s | BGM music_v2 ready (≥%.0fs) → %s | style=%s', (state.page_ctx.page_id or 'PAGE').upper(), state._music_min, Path(state._music_bed).name, state._music_style)
            if state.cost_tracker is not None:
                try:
                    from agents.media.audio_engine import LAST_MUSIC_COMPOSE_MODE
                    state.LAST_MUSIC_COMPOSE_MODE = LAST_MUSIC_COMPOSE_MODE
                    state._mode = str(state.LAST_MUSIC_COMPOSE_MODE or '').strip().lower()
                    state.cost_tracker.track_music(api=state._mode not in ('', 'local', 'ambient', 'none'))
                except Exception:
                    state.cost_tracker.track_music(api=False)
        state._sfx_out = state._reel_dir / f'{state.stem}_v{state.variant + 1:02d}_atmosphere_sfx.mp3'
        state._sfx_prompt = state.page_ctx.ambient_sfx_prompt if state.page_ctx else ''
        if state._early_mode != 'v2':
            state._sfx_loop_path = generate_ambient_track(state._sfx_out, duration_seconds=10.0, prompt=state._sfx_prompt or None)
        if state._sfx_loop_path is not None:
            _LOG.info('%s | Atmosphere SFX 10s → %s (loop 100%%)', (state.page_ctx.page_id or 'PAGE').upper(), Path(state._sfx_loop_path).name)
            if state.cost_tracker is not None and state._early_mode != 'v2' and (Path(state._sfx_loop_path).resolve() == Path(state._sfx_out).resolve()):
                state.cost_tracker.track_sfx(calls=1)
            elif state.cost_tracker is not None and state._early_mode == 'v2':
                state.cost_tracker.track_sfx(calls=1)
    elif app_config.ELEVENLABS_API_KEY and state._early_mode == 'ambient':
        state._ambient_path = state._early_music_box.get('ambient')
        state._ambient_out = state._reel_dir / f'{state.stem}_v{state.variant + 1:02d}_ambient.mp3'
        if state._ambient_path is not None and Path(state._ambient_path).is_file():
            state._sfx_loop_path = Path(state._ambient_path)
            if state.cost_tracker is not None:
                state.cost_tracker.track_sfx(calls=1)
    elif app_config.ELEVENLABS_API_KEY:
        state._ambient_out = state._reel_dir / f'{state.stem}_v{state.variant + 1:02d}_ambient.mp3'
        state._sfx_prompt = state.page_ctx.ambient_sfx_prompt if state.page_ctx else ''
        state._ambient_path = generate_ambient_track(state._ambient_out, duration_seconds=min(12.0, float(state._target_dur)), prompt=state._sfx_prompt or None)
        if state._ambient_path is not None and Path(state._ambient_path).is_file():
            state._sfx_loop_path = Path(state._ambient_path)
            if state.cost_tracker is not None and Path(state._ambient_path).resolve() == Path(state._ambient_out).resolve():
                state.cost_tracker.track_sfx(calls=1)
    if state.page_ctx and (state._ambient_path is None or not Path(state._ambient_path).is_file()):
        state._local_ambient = state.page_ctx.ambient_audio_path
        state._ban_rain = ('rain', 'martial_loop', 'storm', 'thunder', 'hiss')
        if state._local_ambient is not None and state._local_ambient.is_file() and (not any((b in state._local_ambient.name.lower() for b in state._ban_rain))):
            state._ambient_path = state._local_ambient
            _LOG.info('%s | Using local ambient fallback: %s', (state.page_ctx.page_id or '').upper(), state._local_ambient.name)
        elif state._local_ambient is not None and state._local_ambient.is_file() and any((b in state._local_ambient.name.lower() for b in state._ban_rain)):
            _LOG.warning('%s | Ignoring rain/hiss ambient asset %s — using cinematic SFX/drone', (state.page_ctx.page_id or '').upper(), state._local_ambient.name)
        elif (state.page_ctx.page_id or '').lower() == 'ancient_knowledge':
            state._legacy_mystery = app_config.ENGINE_ROOT / 'assets' / 'audio' / 'ambient_mystery_loop.mp3'
            if state._legacy_mystery.is_file():
                state._ambient_path = state._legacy_mystery
                _LOG.info('ANCIENT_KNOWLEDGE | Local ambient mystery fallback: %s', state._legacy_mystery.name)
    if state._ambient_path is not None and Path(state._ambient_path).is_file():
        try:
            from agents.media.audio_engine import resample_audio_48k as _rs48
            state._rs48 = _rs48
            state._amb_src = Path(state._ambient_path)
            if state._amb_src.resolve().parent != state._reel_dir.resolve():
                state._amb_copy = state._reel_dir / f'{state.stem}_v{state.variant + 1:02d}_ambient_48k.mp3'
                import shutil as _sh_amb
                state._sh_amb = _sh_amb
                state._sh_amb.copy2(state._amb_src, state._amb_copy)
                state._ambient_path = state._rs48(state._amb_copy, apply_loudnorm=True)
            else:
                state._ambient_path = state._rs48(state._amb_src, apply_loudnorm=True)
        except Exception as _amb_rs_exc:
            state._amb_rs_exc = _amb_rs_exc
            _LOG.debug('Ambient 48k resample skipped: %s', state._amb_rs_exc)
    state._hook_slug = ''.join((c if c.isalnum() else '_' for c in state.overlay_text.lower())).strip('_')[:32] if state.overlay_text else state.stem
    state._reel_target = state._reel_dir / f'reel_{state._hook_slug}_v{state.variant + 1:02d}.mp4'
    state._reel_dur = max(30.0, state.page_ctx.reel_duration if state.page_ctx else 30.0)
    try:
        _stage_compile_reel_01(state)
        _stage_compile_reel_02(state)
    except Exception as reel_exc:
        state.reel_exc = reel_exc
        _stage_compile_reel_03(state)
