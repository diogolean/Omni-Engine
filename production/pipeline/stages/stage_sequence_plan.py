# -*- coding: utf-8 -*-
"""Narration, beats, and the asset pool for a sequence reel."""
from __future__ import annotations

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

from production.pipeline.stages.stage_sequence_render import _stage_sequence_render_01, _stage_sequence_render_02, _stage_sequence_render_03, _stage_sequence_render_04

def _stage_sequence_plan_01(state: VariantState) -> None:
    """Variant worker stage. Original lines 1271-1598."""
    state._ak_base_style = state.page_ctx.illustration_style.rstrip(' .') if state.page_ctx.illustration_style else state.page_ctx.atmosphere_style.rstrip(' .')
    state._is_mm = (state.page_ctx.page_id or '').lower() == 'master_mei'
    state._seq_words = state.page_ctx.reel_narration_words if state.page_ctx else app_config.words_for_duration(80.0)
    state._seq_min = state.page_ctx.reel_narration_min_words if state.page_ctx else app_config.words_for_duration(80.0)
    state._planned_scene_durs = None
    if state._is_mm:
        from production.writers.mei_narrative import resolve_mei_duration_profile
        state.resolve_mei_duration_profile = resolve_mei_duration_profile
        state._mei_prof = state.resolve_mei_duration_profile(state.page_ctx.reel_duration)
        state._seq_n = int(state._mei_prof['frames'])
        state._seq_words = int(state._mei_prof['words_target'])
        state._seq_min = int(state._mei_prof['words_min'])
        _LOG.info('Master Mei duration profile | target=%ss words=%d–%d (tgt %d) frames=%d max_consec_training=%d', state._mei_prof['target_s'], state._mei_prof['words_min'], state._mei_prof['words_max'], state._mei_prof['words_target'], state._mei_prof['frames'], state._mei_prof['max_consec_training'])
    else:
        state._tier1_body_target = None
        if state.post_type == 'WAN_REEL':
            from production.media.scene_pacing import parse_scene_duration as _parse_wan_scene_dur
            state._parse_wan_scene_dur = _parse_wan_scene_dur
            state._wan_spec = state._parse_wan_scene_dur(state.page_ctx.wan_scene_duration)
            state._wan_hold = float(state._wan_spec.fixed_s) if state._wan_spec.mode == 'fixed' and float(state._wan_spec.fixed_s) > 0 else 7.0
            state._wan_dur = float(getattr(state.page_ctx, 'wan_reel_duration', None) or state.page_ctx.reel_duration or 70.0)
            state._seq_n = max(8, min(14, int(round(state._wan_dur / state._wan_hold))))
            state._planned_scene_durs = [state._wan_hold] * state._seq_n
            _LOG.info('WAN_REEL scene pacing | %s hold=%.1fs duration=%.1fs → %d acts (ECONOMIC_REEL two-tier unused)', state.page_ctx.wan_scene_duration, state._wan_hold, state._wan_dur, state._seq_n)
        elif getattr(state.page_ctx, 'use_two_tier_pacing', False):
            from production.media.reel_sequence_engine import compute_two_tier_act_count as _compute_two_tier_act_count
            state._compute_two_tier_act_count = _compute_two_tier_act_count
            state._tier1_n, state._tier2_n, state._seq_n = state._compute_two_tier_act_count(state.page_ctx.reel_duration, tier1_max_acts=state.page_ctx.reel_tier1_max_acts, tier1_horizon_s=state.page_ctx.reel_tier1_horizon_s, tier1_seconds_per_act=state.page_ctx.reel_tier1_seconds_per_act, tier2_seconds_per_act=state.page_ctx.reel_tier2_seconds_per_act, min_acts=state.page_ctx.reel_image_min_count)
            state._tier1_body_target = max(0, state._tier1_n - 3)
            _LOG.info('TWO-TIER PACING | duration=%.1fs → tier1=%d acts (hook 3 + body %d) + tier2=%d acts (body 8-12s) = %d total | tier1_horizon=%.0fs tier1_spa=%.1fs tier2_spa=%.1fs (no static ceiling)', state.page_ctx.reel_duration, state._tier1_n, state._tier1_body_target, state._tier2_n, state._seq_n, state.page_ctx.reel_tier1_horizon_s, state.page_ctx.reel_tier1_seconds_per_act, state.page_ctx.reel_tier2_seconds_per_act)
        elif state.page_ctx.uses_plan_scenes_pacing:
            state._planned_scene_durs = _plan_scenes(state.page_ctx.reel_duration, state.page_ctx.scene_duration, progressive_start_s=state.page_ctx.scene_progressive_start_s, progressive_step_every=state.page_ctx.scene_progressive_step_every, progressive_step_s=state.page_ctx.scene_progressive_step_s, progressive_cap_s=state.page_ctx.scene_progressive_cap_s, min_scenes=state.page_ctx.reel_image_min_count, max_scenes=state.page_ctx.reel_image_count)
            state._seq_n = len(state._planned_scene_durs)
        elif state.page_ctx.reel_use_hook_body_pacing:
            state._seq_n = _compute_hook_body_act_count(state.page_ctx.reel_duration, hook_hold_s=state.page_ctx.reel_hook_hold_s, body_hold_s=state.page_ctx.reel_body_hold_s, min_acts=state.page_ctx.reel_image_min_count, max_acts=state.page_ctx.reel_image_count)
        else:
            state._seq_n = _compute_dense_act_count(state.page_ctx.reel_duration, seconds_per_act=state.page_ctx.reel_seconds_per_act, min_acts=state.page_ctx.reel_image_min_count, max_acts=state.page_ctx.reel_image_count)
        state._scene_len = state.page_ctx.scene_length
        if state._scene_len and state.post_type != 'WAN_REEL' and (not getattr(state.page_ctx, 'use_two_tier_pacing', False)):
            state._seq_n = max(state.page_ctx.reel_image_min_count, min(state.page_ctx.reel_image_count, max(2, int(round(state.page_ctx.reel_duration / float(state._scene_len))))))
            _LOG.info('SCENE_LENGTH override | %.1fs/still → %d acts', float(state._scene_len), state._seq_n)
    assert state._episode_assets_dir is not None
    state._reel_img_dir = state._episode_assets_dir / 'work'
    state._reel_img_dir.mkdir(parents=True, exist_ok=True)
    if state._is_mm:
        state._pace_label = 'mei_profile'
    elif state.post_type == 'WAN_REEL':
        state._pace_label = f'wan_fixed:{state.page_ctx.wan_scene_duration} n={state._seq_n}'
    elif getattr(state.page_ctx, 'use_two_tier_pacing', False):
        state._pace_label = f'two-tier tier1={getattr(state, '_tier1_n', 0)}@{state.page_ctx.reel_tier1_seconds_per_act:.1f}s tier2={getattr(state, '_tier2_n', 0)}@{state.page_ctx.reel_tier2_seconds_per_act:.1f}s'
    elif getattr(state, '_planned_scene_durs', None):
        state._pace_label = f'plan_scenes:{state.page_ctx.scene_duration} | {_describe_scene_plan(state._planned_scene_durs)}'
    elif state.page_ctx.reel_use_hook_body_pacing:
        state._pace_label = f'hook={state.page_ctx.reel_hook_hold_s:.1f}s body={state.page_ctx.reel_body_hold_s:.1f}s'
    else:
        state._pace_label = f'dense={state.page_ctx.reel_seconds_per_act:.1f}s/act'
    _LOG.info('Sequence reel | acts=%d | video_length=%.0fs (window %.0f–%.0fs) | pacing=%s | episode=%s | work=%s', state._seq_n, state.page_ctx.reel_duration, state.page_ctx.reel_duration_target_min, state.page_ctx.reel_duration_target_max, state._pace_label, state._episode_id, state._reel_img_dir)
    if state.caption_engine is not None:
        try:
            if state.page_ctx and (state.page_ctx.page_id or '').lower() == 'ancient_knowledge':
                from production.formats.economic_reel.artefact.policy import artefact_images_enabled as _pool_flag
                state._pool_flag = _pool_flag
                if state._pool_flag():
                    from production.formats.economic_reel.artefact.asset_pool import build_topic_pool as _build_topic_pool
                    state._build_topic_pool = _build_topic_pool
                    state._asset_pool = state._build_topic_pool(str(state.resolved_subject or ''), 'ancient_knowledge', state._reel_img_dir / 'pool')
                    if not state._asset_pool:
                        from production.formats.economic_reel.artefact.asset_pool import PoolTooSmall as _PoolTooSmall
                        state._PoolTooSmall = _PoolTooSmall
                        raise state._PoolTooSmall('asset pool empty after cache, search, and nano')
                    _LOG.info('ASSET POOL | n=%d | %s', len(state._asset_pool), ' | '.join((str(getattr(item, 'caption', '') or '')[:40] for item in state._asset_pool[:6])))
                    try:
                        from production.formats.economic_reel.shot_planner.entities import entity_for_line as _pool_entity
                        state._pool_entity = _pool_entity
                        from production.formats.economic_reel.shot_planner.entities import load_entities as _pool_entities
                        state._pool_entities = _pool_entities
                        state._pool_rows = state._pool_entities('ancient_knowledge')
                        state._pool_ent = state._pool_entity(str(state.resolved_subject or ''), state._pool_rows, topic=str(state.resolved_subject or ''))
                        state._pool_geography = state._pool_ent.geography if state._pool_ent is not None else ''
                    except Exception:
                        state._pool_geography = ''
            state._seq_reject = ''
            state._hooks_for_seq = list(state.generated_hooks_cache or [])
            state._asset_captions = [str(getattr(item, 'caption', '') or '') for item in state._asset_pool if str(getattr(item, 'caption', '') or '').strip()]
            for _seq_attempt in range(1, MAX_UNIQUENESS_RETRIES + 1):
                state._seq_attempt = _seq_attempt
                state._early_seq_script = state.caption_engine.generate_sequence_voiceover(state.resolved_subject, page_niche=state.page_ctx.content_niche, persona_voice=state.page_ctx.tts_voice_preference or ('calm, deep, highly wise, authoritative ancient master' if state._is_mm else 'investigative, neutral, immersive'), n_acts=state._seq_n, duration_s=state.page_ctx.reel_duration, total_words_target=state._seq_words, economic=state.economic, niche_disclaimer=state.page_ctx.niche_disclaimer, cta_line='', narrative_mode=state.page_ctx.narrative_mode, batch_angle_block=state._batch_angle_block, uniqueness_rejection=state._seq_reject, previously_generated_hooks=state._hooks_for_seq, asset_captions=state._asset_captions or None) or ''
                if state._early_seq_script:
                    _LOG.info('SEQUENCE_REEL | early script drafted (%d words) — seed only, downstream measures actual TTS duration.', len(state._early_seq_script.split()))
                if state.uniqueness_guard is None or not state._early_seq_script:
                    break
                state._opening = ' '.join((state._early_seq_script or '').split()[:80])
                state._ok_s, state._prior_s, state._score_s = state.uniqueness_guard.try_claim(state._opening)
                if state._ok_s:
                    _LOG.info('SEQUENCE uniqueness OK | variant=%d attempt=%d', state.variant + 1, state._seq_attempt)
                    break
                state._seq_reject = state.uniqueness_guard.rejection_instruction(state._prior_s or '', state._score_s)
                _LOG.warning('SEQUENCE uniqueness REJECT | variant=%d attempt=%d sim=%.0f%%', state.variant + 1, state._seq_attempt, state._score_s * 100)
                if state._seq_attempt >= MAX_UNIQUENESS_RETRIES:
                    state.uniqueness_guard.register(state._opening)
                    break
            if state._early_seq_script:
                if state.page_ctx and (state.page_ctx.page_id or '').lower() == 'ancient_knowledge':
                    from production.formats.economic_reel.artefact.script_length import AK_NARRATION_S, soft_ceiling
                    state.AK_NARRATION_S = AK_NARRATION_S
                    state.soft_ceiling = soft_ceiling
                    state._early_seq_script = state.soft_ceiling(state._early_seq_script, max_seconds=state.AK_NARRATION_S)
                else:
                    state._early_seq_script = _trim_script_to_word_limit(state._early_seq_script, max_words=state._seq_words + 15)
                if state.cost_tracker is not None:
                    state.cost_tracker.track_text(char_count=len(state._early_seq_script))
                _LOG.info('SEQUENCE_REEL | early script ready for act-image sync (%d words, %d acts)', len(state._early_seq_script.split()), state._seq_n)
        except Exception as _early_exc:
            state._early_exc = _early_exc
            if state.page_ctx and (state.page_ctx.page_id or '').lower() == 'ancient_knowledge':
                raise
            _LOG.warning('SEQUENCE_REEL early script failed (%s) — falling back to topic.', state._early_exc)
            state._early_seq_script = ''
    state._ak_page = bool(state.page_ctx and (state.page_ctx.page_id or '').lower() == 'ancient_knowledge')

def _stage_sequence_plan_02(state: VariantState) -> None:
    """Variant worker stage. Original lines 1599-1957."""
    if state._ak_page:
        from production.formats.economic_reel.artefact.script_grounding import grounding_problem as _grounding_problem
        state._grounding_problem = _grounding_problem
        from production.formats.economic_reel.artefact.script_grounding import script_is_usable as _script_usable
        state._script_usable = _script_usable
        state._topic_text = str(state.resolved_subject or '')
        if state._early_seq_script and state._grounding_problem(state._early_seq_script, state._topic_text, channel='ancient_knowledge'):
            state._ground_why = state._grounding_problem(state._early_seq_script, state._topic_text, channel='ancient_knowledge')
            state._retry_note = state._ground_why
            if state._ground_why == 'hook does not name the topic':
                state._retry_note = f'The first sentence must contain "{state._topic_text}".'
            elif state._ground_why.startswith('camera'):
                state._retry_note = 'Rewrite without camera directions. Do not mention fog rolling, golden hour, aerial views, foliage, rock formations, or water flowing as a shot.'
            elif state._ground_why.startswith('tone'):
                state._retry_note = "Rewrite in a National Geographic tone. Use 'some researchers suggest'. Do not say archaeology refuses, and do not say a building powered devices."
            _LOG.warning('SCRIPT GROUNDING | %s | one retry', state._ground_why)
            if state.caption_engine is not None:
                state._early_seq_script = state.caption_engine.generate_sequence_voiceover(state.resolved_subject, page_niche=state.page_ctx.content_niche, persona_voice=state.page_ctx.tts_voice_preference or 'investigative, neutral, immersive', n_acts=state._seq_n, duration_s=state.page_ctx.reel_duration, total_words_target=state._seq_words, economic=state.economic, niche_disclaimer=state.page_ctx.niche_disclaimer, cta_line='', narrative_mode=state.page_ctx.narrative_mode, batch_angle_block=state._batch_angle_block, uniqueness_rejection=state._retry_note, previously_generated_hooks=list(state.generated_hooks_cache or []), asset_captions=state._asset_captions or None) or ''
                if state._early_seq_script:
                    from production.formats.economic_reel.artefact.script_length import AK_NARRATION_S, soft_ceiling
                    state.AK_NARRATION_S = AK_NARRATION_S
                    state.soft_ceiling = soft_ceiling
                    state._early_seq_script = state.soft_ceiling(state._early_seq_script, max_seconds=state.AK_NARRATION_S)
        state._ground_left = state._grounding_problem(state._early_seq_script or '', state._topic_text, channel='ancient_knowledge') if state._early_seq_script else 'empty script'
        if state._ground_left == 'hook does not name the topic' and state._script_usable(state._early_seq_script or '', state._topic_text):
            from production.formats.economic_reel.artefact.script_grounding import repair_hook as _repair_hook
            state._repair_hook = _repair_hook
            state._early_seq_script = state._repair_hook(state._early_seq_script, state._topic_text)
            state._ground_left = state._grounding_problem(state._early_seq_script, state._topic_text, channel='ancient_knowledge')
            _LOG.info('SCRIPT GROUNDING | hook repaired | %s', state._topic_text[:80])
        if not state._script_usable(state._early_seq_script or '', state._topic_text) or state._ground_left:
            state._saved_script = ''
            try:
                from production.formats.economic_reel.artefact.checkpoint import ReelCheckpoint as _ScriptCkpt
                state._ScriptCkpt = _ScriptCkpt
                state._saved_script = state._ScriptCkpt.for_reel('ancient_knowledge', state.stem).load_script()
            except Exception:
                state._saved_script = ''
            state._saved_ok = state._script_usable(state._saved_script, state._topic_text) and (not state._grounding_problem(state._saved_script, state._topic_text, channel='ancient_knowledge'))
            if state._saved_ok:
                state._early_seq_script = state._saved_script
                _LOG.info('SCRIPT resume | words=%d', len(state._saved_script.split()))
            else:
                raise RuntimeError('narration script empty or ungrounded after retries — refusing to plan images from the topic' + (f' ({state._ground_left})' if state._ground_left else ''))
        else:
            try:
                from production.formats.economic_reel.artefact.provider_health import text_writer_banner
                state.text_writer_banner = text_writer_banner
                state._writer = state.text_writer_banner()
                if state._writer:
                    state.bm.humanizer_summary = state._writer
                    state.humanizer_notes = state._writer
                    _LOG.info('Models banner | humanizer=%s', state._writer)
            except Exception:
                pass
            try:
                from production.formats.economic_reel.artefact.checkpoint import ReelCheckpoint as _ScriptCkpt
                state._ScriptCkpt = _ScriptCkpt
                state._ScriptCkpt.for_reel('ancient_knowledge', state.stem).save_script(state._early_seq_script)
            except Exception:
                pass
    state._beat_source = state._early_seq_script if state._ak_page else state._early_seq_script or (state.caption if state.caption and state.caption != '(skipped)' else state.resolved_subject)
    state._spoken_snippets = _segment_script_into_act_snippets(state._beat_source, state._seq_n)
    state._audio_first_scenes = []
    if state.post_type in ('ECONOMIC_REEL', 'WAN_REEL'):
        state._clips_dir = Path(app_config.PAGE_OUTPUTS_DIR) / 'clips'
        state._clips_dir.mkdir(parents=True, exist_ok=True)
        state._voice_path, state._narr_word_timings, state._narr_dur, state._ak_total_audio, state._early_seq_script, state._cta_audio_path, state._cta_audio_dur, state._cta_silence_s = _synthesize_sequence_voice_track(state._beat_source, reel_dir=state._clips_dir, stem=state.stem, variant=state.variant, page_ctx=state.page_ctx, caption_engine=state.caption_engine, resolved_subject=state.resolved_subject, economic=state.economic, n_acts=state._seq_n, batch_angle_block=state._batch_angle_block, generated_hooks_cache=state.generated_hooks_cache, cost_tracker=state.cost_tracker)
        _commit_reel_angle(state.caption_engine, state.page_ctx, state.resolved_subject, state._early_seq_script)
        if state._voice_path is not None and state._narr_dur > 0.5:
            state._ak_voice_ready = True
            if state.cost_tracker is not None:
                state._early_tts_chars = len(state._early_seq_script or '')
                state._early_cta = (state.page_ctx.reel_cta_text if state.page_ctx else '') or ''
                state._early_tts_chars += len(state._early_cta)
                if state._early_tts_chars > 0:
                    from production.formats.economic_reel.artefact.checkpoint import tts_was_resumed as _tts_resumed
                    state._tts_resumed = _tts_resumed
                    if not state._tts_resumed():
                        state.cost_tracker.track_audio(char_count=state._early_tts_chars, sfx=False)
            from production.media.reel_sequence_engine import plan_bucket_act_durations as _plan_bucket_act_durations
            state._plan_bucket_act_durations = _plan_bucket_act_durations
            state._pre_snippets = _segment_script_into_act_snippets(state._early_seq_script or state.resolved_subject, state._seq_n)
            state._pre_snippets = [s or state.resolved_subject for s in state._pre_snippets]
            if state.post_type == 'WAN_REEL':
                state._audio_total = float(getattr(state, '_ak_total_audio', None) or 0.0) or float(state._narr_dur) + float(state._cta_audio_dur or 0.0) + float(state._cta_silence_s or 1.0)
                state._wan_n = max(8, min(14, int(round(state._audio_total / 7.0)))) if state._audio_total > 0.5 else state._seq_n
                if state._wan_n != state._seq_n:
                    _LOG.info('WAN_REEL act recount after TTS | audio=%.1fs %d→%d acts (~7s)', state._audio_total, state._seq_n, state._wan_n)
                    state._seq_n = state._wan_n
                    state._pre_snippets = _segment_script_into_act_snippets(state._early_seq_script or state.resolved_subject, state._seq_n)
                    state._pre_snippets = [s or state.resolved_subject for s in state._pre_snippets]
                state._unit = state._audio_total / state._seq_n if state._seq_n else 7.0
                state._ak_act_durs = [state._unit] * state._seq_n
                state._spoken_snippets = list(state._pre_snippets)
                state._ak_keep_map = list(range(state._seq_n))
                _LOG.info('WAN_REEL equal holds | n=%d hold=%.2fs sum=%.1fs (target ~7s)', state._seq_n, state._unit, state._unit * state._seq_n)
            else:
                if not state._is_mm and state._narr_word_timings:
                    try:
                        from production.qa.beats import _scene_for as _sentence_scene, beats_from_timestamps
                        state._sentence_scene = _sentence_scene
                        state.beats_from_timestamps = beats_from_timestamps
                        state._cta_len = float(state._cta_audio_dur or 0.0)
                        state._silence = float(state._cta_silence_s or 0.0) if state._cta_len > 0.05 else 0.0
                        state._timeline_audio = float(getattr(state, '_ak_total_audio', None) or 0.0)
                        if state._timeline_audio <= 0.5:
                            state._timeline_audio = float(state._narr_dur or 0.0) + state._silence + state._cta_len
                        state._tile_end = float(state._narr_dur or 0.0) + state._silence if state._cta_len > 0.05 else state._timeline_audio
                        state._rows = state.beats_from_timestamps(state._early_seq_script or state.resolved_subject, state._narr_word_timings, narration_s=float(state._narr_dur or 0.0), audio_end=state._tile_end)
                        if state._rows:
                            state._audio_first_scenes = []
                            state._ak_act_durs = []
                            state._sentence_starts = []
                            for _i, (_text, _start, _end) in enumerate(state._rows):
                                state._i = _i
                                state._text = _text
                                state._start = _start
                                state._end = _end
                                state._dur = max(0.05, float(state._end) - float(state._start))
                                state._ak_act_durs.append(state._dur)
                                state._sentence_starts.append(float(state._start))
                                state._audio_first_scenes.append(state._sentence_scene(state._text, float(state._start), float(state._start) + state._dur, index=state._i, inherited={'domain_anchors': state.resolved_subject}))
                            state._spoken_snippets = [str(s.get('spoken_text') or state.resolved_subject) for s in state._audio_first_scenes]
                            state._seq_n = len(state._audio_first_scenes)
                            state._ak_keep_map = list(range(state._seq_n))
                            _LOG.info('SENTENCE BEATS | n=%d durs=%s', state._seq_n, ','.join((f'{d:.2f}' for d in state._ak_act_durs[:8])))
                            for _sc in state._audio_first_scenes:
                                state._sc = _sc
                                _LOG.info('SENTENCE BEAT | %s [%.1f–%.1f] %s', state._sc.get('scene_index'), float(state._sc.get('start_time') or 0), float(state._sc.get('end_time') or 0), str(state._sc.get('spoken_text') or '')[:80])
                    except Exception as _chunk_exc:
                        state._chunk_exc = _chunk_exc
                        _LOG.warning('SENTENCE BEATS failed (%s) — bucket planner', state._chunk_exc)
                        state._audio_first_scenes = []
                        state._sentence_starts = []
                if not state._audio_first_scenes:
                    state._bucket_kwargs = {}
                    state._t1_body_local = getattr(state, '_tier1_body_target', None)
                    if getattr(state.page_ctx, 'use_two_tier_pacing', False) and state._t1_body_local is not None and (int(state._t1_body_local) > 0):
                        state._bucket_kwargs['tier2_body_start'] = int(state._t1_body_local)
                    state._ak_act_durs, state._spoken_snippets, state._ak_keep_map = state._plan_bucket_act_durations(state._pre_snippets, narration_s=float(state._narr_dur), cta_audio_s=float(state._cta_audio_dur or 0.0), silence_before_cta_s=float(state._cta_silence_s or 1.0), **state._bucket_kwargs)
            state._ak_bucket_snippets = list(state._spoken_snippets)
            state._spoken_snippets = [s or state.resolved_subject for s in state._spoken_snippets]
            if len(state._ak_act_durs) != state._seq_n:
                _LOG.info('Bucket planner | merged %d short act(s) → keeping %d/%d stills', state._seq_n - len(state._ak_act_durs), len(state._ak_act_durs), state._seq_n)
                state._seq_n = len(state._ak_act_durs)
            _LOG.info('BUCKET PLAN | n=%d durs=%s beats=%s', state._seq_n, ','.join((f'{d:.2f}' for d in state._ak_act_durs)), ' | '.join((f'{i + 1}[{len((state._ak_bucket_snippets[i] or '').split())}w→{state._ak_act_durs[i]:.1f}s]:{(state._ak_bucket_snippets[i] or '')[:40]}' for i in range(state._seq_n))))
    if _is_sequence_video_post(state.post_type):
        from production.qa.beats import refine_reel_beats
        state.refine_reel_beats = refine_reel_beats
        state._beat_plan = state.refine_reel_beats(snippets=list(state._spoken_snippets or []), durations=list(state._ak_act_durs or []), word_timings=list(state._narr_word_timings or []), narration_s=float(state._narr_dur or 0.0), scenes=list(state._audio_first_scenes or []), topic=str(state.resolved_subject or ''))
        if state._beat_plan.changed:
            state._spoken_snippets = list(state._beat_plan.snippets)
            state._ak_act_durs = list(state._beat_plan.durations)
            state._seq_n = len(state._spoken_snippets)
            state._audio_first_scenes = list(state._beat_plan.scenes)
            _LOG.info('REEL BEATS | n=%d max_s=%.1f durs=%s', state._seq_n, max(state._beat_plan.durations or [0.0]), ','.join((f'{d:.2f}' for d in (state._beat_plan.durations or [])[:8])))
    state._cta_beat_index = None

def _stage_sequence_plan_03(state: VariantState) -> None:
    """Variant worker stage. Original lines 1958-3264."""
    if state.page_ctx and (state.page_ctx.page_id or '').lower() == 'ancient_knowledge' and state._spoken_snippets and state._ak_act_durs:
        state._cta_line = (state.page_ctx.reel_cta_text or '').strip()
        if state._sentence_starts and float(state._timeline_audio or 0.0) > 0.5:
            from production.qa.beats import seal_sentence_timeline as _seal_tl
            state._seal_tl = _seal_tl
            state._spoken_snippets, state._ak_act_durs = state._seal_tl(list(state._spoken_snippets), list(state._ak_act_durs), list(state._sentence_starts), float(state._timeline_audio), cta_text=state._cta_line)
            state._seq_n = len(state._spoken_snippets)
            _LOG.info('TIMELINE | beats=%d video=%.2fs audio=%.2fs', len(state._ak_act_durs), sum(state._ak_act_durs), float(state._timeline_audio))
        try:
            from production.qa.cta_beat import with_cta_beat as _with_cta
            state._with_cta = _with_cta
            state._cta_hold = float(getattr(state, '_cta_audio_dur', None) or 0.0) + float(getattr(state, '_cta_silence_s', None) or 1.0)
            if state._cta_line:
                state._spoken_snippets, state._ak_act_durs, state._cta_beat_index = state._with_cta(list(state._spoken_snippets), list(state._ak_act_durs), state._cta_line, state._cta_hold)
                state._seq_n = len(state._spoken_snippets)
                _LOG.info('CTA BEAT | index=%s hold=%.2fs line=%.60s', state._cta_beat_index, state._cta_hold, state._cta_line)
        except Exception as _cta_beat_exc:
            state._cta_beat_exc = _cta_beat_exc
            _LOG.warning('CTA beat skipped (%s)', state._cta_beat_exc)
    if state._asset_pool and state._spoken_snippets:
        try:
            from production.formats.economic_reel.artefact.asset_pool import fit_pool_beats as _fit_beats
            state._fit_beats = _fit_beats
            state._protect_cta = 1 if state._cta_beat_index is not None and int(state._cta_beat_index) == len(state._spoken_snippets) - 1 else 0
            state._fit_snips, state._fit_durs, state._asset_assignments = state._fit_beats(list(state._spoken_snippets), list(state._ak_act_durs or []), list(state._asset_pool), geography=state._pool_geography, flux_max=6, protect_tail=state._protect_cta)
            state._durs_match = bool(state._ak_act_durs) and abs(sum(state._fit_durs) - sum(state._ak_act_durs)) < 0.05
            if state._fit_snips and state._fit_durs and (len(state._fit_snips) == len(state._fit_durs) == len(state._asset_assignments)) and state._durs_match:
                state._spoken_snippets = list(state._fit_snips)
                state._ak_act_durs = list(state._fit_durs)
                state._seq_n = len(state._spoken_snippets)
                if state._protect_cta:
                    state._cta_beat_index = len(state._spoken_snippets) - 1
            elif len(state._asset_assignments) != len(state._spoken_snippets):
                _LOG.warning('ASSET SHOTS length mismatch | shots=%d beats=%d', len(state._asset_assignments), len(state._spoken_snippets))
                state._asset_assignments = []
            _LOG.info('ASSET SHOTS | n=%d flux=%d', len(state._asset_assignments), sum((1 for item in state._asset_assignments if item.mode == 'flux')))
        except Exception as _assign_exc:
            state._assign_exc = _assign_exc
            _LOG.warning('ASSET SHOTS skipped (%s)', state._assign_exc)
            state._asset_assignments = []
    if not state._is_mm and (not state._ak_voice_ready):
        state._sequence_image_paths.append(_ensure_sequence_image(state.raw_bg_path, fallback=state.raw_bg_path))
    state._act1_base = state.raw_bg_path
    if state._is_mm:
        _stage_sequence_render_01(state)
    else:
        _stage_sequence_render_02(state)
        _stage_sequence_render_03(state)
        _stage_sequence_render_04(state)
    if state._sequence_image_paths:
        if not state.img_path_display:
            state.img_path_display = state._sequence_image_paths[0]
        _LOG.info('Sequence reel | frames=%d | api_image_units=%d', len(state._sequence_image_paths), state._images_generated_this_variant)
        if state.post_type == 'WAN_REEL':
            state._wan_stage_times['script_tts_stills_s'] = round(time.monotonic() - state._wan_t0_variant, 2)
            _LOG.info('WAN_REEL | script+TTS+stills wall=%.1fs | frames=%d', state._wan_stage_times['script_tts_stills_s'], len(state._sequence_image_paths))
    if state._is_mm and state._sequence_image_paths and (state.adapter is not None) and (len(state._sequence_image_paths) >= 2):
        try:
            from production.qa.judges.visual_inspector import inspect_sequence_images
            state.inspect_sequence_images = inspect_sequence_images
            from production.media.visual_roles import ROLE_MASTER, assign_frame_beats, build_role_prompt
            state.ROLE_MASTER = ROLE_MASTER
            state.assign_frame_beats = assign_frame_beats
            state.build_role_prompt = build_role_prompt
            state._qa_timeout = float(getattr(app_config, 'VISUAL_EVAL_TIMEOUT_S', 10) or 10)
            state._insp_wall = max(12.0, state._qa_timeout + 2.0)
            state._insp_pool = ThreadPoolExecutor(max_workers=1)
            try:
                state._insp = state._insp_pool.submit(state.inspect_sequence_images, state._sequence_image_paths, use_vlm=True).result(timeout=state._insp_wall)
            except Exception as _insp_to:
                state._insp_to = _insp_to
                state._why = str(state._insp_to).strip() or type(state._insp_to).__name__
                _LOG.warning('Visual Inspector non-blocking: timed out/failed (%s) — proceeding to render', state._why)
                state._insp = None
            finally:
                state._insp_pool.shutdown(wait=False, cancel_futures=True)
            if state._insp is None:
                _LOG.info('VISUAL_INSPECTOR | skipped (fail-open) — compiling as-is')
                state._insp_notes = []
                state._insp_regen = []
            else:
                state._insp_notes = list(state._insp.notes or [])
                state._insp_regen = list(state._insp.regenerate_indices or [])
                _LOG.info('VISUAL_INSPECTOR | passed=%s regen_frames=%s | %s', state._insp.passed, [i + 1 for i in state._insp_regen], '; '.join(state._insp_notes[:4]) if state._insp_notes else 'ok')
            if any(('visual qa skipped' in (n or '').lower() for n in state._insp_notes)):
                state._insp_regen = []
            state._beats_insp = state.assign_frame_beats(len(state._sequence_image_paths))
            for _ri in state._insp_regen[:5]:
                state._ri = _ri
                if state._ri < 0 or state._ri >= len(state._sequence_image_paths):
                    continue
                state._beat = state._beats_insp[state._ri] if state._ri < len(state._beats_insp) else ''
                state._role = 'master' if state._beat in ('intro', 'outro', 'training') else 'disciple' if state._beat == 'break_free' else 'slave'
                state._snip = state._spoken_snippets[state._ri] if state._ri < len(state._spoken_snippets) else state.resolved_subject
                state._pos, state._neg = state.build_role_prompt(role=state._role, visual_dna=state.page_ctx.master_mei_visual_dna or _MEI_DNA_FALLBACK if state._role == state.ROLE_MASTER else '', spoken_beat=state._snip, subject=state.resolved_subject, act_index=state._ri, beat=state._beat)
                state._av_ref = getattr(state, '_mm_avatar_ref', None)
                state._av_w = getattr(state, '_mm_avatar_w', None)
                state._use_av = state._role == state.ROLE_MASTER and state._av_ref is not None
                try:
                    state._regen_path = state.adapter.generate(state._pos, output_stem=f'{state.stem}_act{state._ri + 1:02d}_regen', output_directory=state._reel_img_dir, reference_image_path=state._av_ref if state._use_av else None, avatar_mode='ON' if state._use_av else 'OFF', reference_image_weight=state._av_w if state._use_av else None, visual_role=state._role, negative_prompt=state._neg or None)
                    state._sequence_image_paths[state._ri] = _ensure_sequence_image(state._regen_path, fallback=state._sequence_image_paths[state._ri], target_path=state._reel_img_dir / f'{state.stem}_act{state._ri + 1:02d}_regen.png')
                    state._images_generated_this_variant += _track_adapter_image(state.cost_tracker, state.adapter)
                    _LOG.info('VISUAL_INSPECTOR | regenerated frame %d (beat=%s role=%s)', state._ri + 1, state._beat, state._role)
                except Exception as _regen_exc:
                    state._regen_exc = _regen_exc
                    _LOG.warning('VISUAL_INSPECTOR | regen frame %d failed: %s', state._ri + 1, state._regen_exc)
        except Exception as _insp_exc:
            state._insp_exc = _insp_exc
            _LOG.warning('VISUAL_INSPECTOR skipped: %s', state._insp_exc)
