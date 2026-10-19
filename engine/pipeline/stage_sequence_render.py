# -*- coding: utf-8 -*-
"""Master Mei and sequence still render, including QA gates."""
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


def _stage_sequence_render_01(state: VariantState) -> None:
    """Variant worker stage. Original lines 2062-2302."""
    try:
        from agents.media.visual_roles import pick_mei_meditation_environment as _mei_pick_env_prod, select_style_references_for_mei_frame as _mei_select_srefs
        state._mei_pick_env_prod = _mei_pick_env_prod
        state._mei_select_srefs = _mei_select_srefs
        state._hook_env = state._mei_pick_env_prod(episode_seed=state.resolved_subject or '', spoken_beat=state._early_seq_script or (state.caption if state.caption and state.caption != '(skipped)' else ''))
    except Exception:
        state._hook_env = 'mist-covered peak cliff above a sea of clouds'

        def _mei_select_srefs(refs, *, act_index, episode_seed=''):
            return [] if int(act_index) == 0 else list(refs or [])
    from agents.media.avatar_engine.mei_visual import MASTER_STYLE_ANCHOR_DEFAULT as _MM_STYLE_DEFAULT_PROD
    state._MM_STYLE_DEFAULT_PROD = _MM_STYLE_DEFAULT_PROD
    from agents.media.style_reader import default_master_mei_ref_folder as _mm_ref_folder_prod
    state._mm_ref_folder_prod = _mm_ref_folder_prod
    state._mm_fixed_anchor = (state.page_ctx.master_style_anchor or state._MM_STYLE_DEFAULT_PROD).strip() or state._MM_STYLE_DEFAULT_PROD
    state._mm_prompts, state._mm_slots, state._mm_roles, state._mm_negatives = _build_master_mei_script_act_prompts(subject=state.resolved_subject, script=state._early_seq_script or (state.caption if state.caption and state.caption != '(skipped)' else state.resolved_subject), n_acts=state._seq_n, base_style=state._ak_base_style, visual_dna=state.page_ctx.master_mei_visual_dna or _MEI_DNA_FALLBACK, hook_env=state._hook_env, master_style_anchor=state._mm_fixed_anchor, reference_folder=state._mm_ref_folder_prod(), use_vision_style=False)
    _LOG.info('MASTER_MEI 3-ROLE distribution | roles=%s | avatar_slots=%s', state._mm_roles, sorted(state._mm_slots))
    state._mm_style_refs = state.page_ctx.resolve_style_reference_images()
    state._mm_style_weight = state.page_ctx.style_reference_weight
    if state._mm_style_refs:
        _LOG.info('MASTER_MEI | %d style reference image(s) loaded (weight=%.2f) → %s', len(state._mm_style_refs), state._mm_style_weight, ', '.join((p.name for p in state._mm_style_refs)))
    state._mm_avatar_ref = None
    state._mm_avatar_w = None
    if not state.page_ctx.sequence_force_avatar_off:
        state._mm_avatar_ref = _assert_mei_avatar(app_config.ENGINE_ROOT)
        if not state._mm_avatar_ref.is_file():
            state._forced_mm = state.page_ctx.forced_avatar_reference_path
            if state._forced_mm is not None and state._forced_mm.is_file():
                state._mm_avatar_ref = state._forced_mm
            else:
                _LOG.error('MASTER_MEI | avatar.png MISSING — Mei frames will fail zero-hallucination lock. Expected: %s', _resolve_mei_avatar_path(app_config.ENGINE_ROOT))
                state._mm_avatar_ref = None
        if state._mm_avatar_ref is not None:
            state._mm_avatar_w = state.page_ctx.avatar_image_weight
            _LOG.info('MASTER_MEI | avatar likeness LOCKED → %s (weight=%.2f)', state._mm_avatar_ref, state._mm_avatar_w or 0.0)
    state._mm_act_meta = {}
    state._mm_act_jobs = {}
    for _act_i in range(0, state._seq_n):
        state._act_i = _act_i
        state._snippet = state._spoken_snippets[state._act_i] if state._act_i < len(state._spoken_snippets) else state.resolved_subject
        state._act_prompt = state._mm_prompts[state._act_i] if state._act_i < len(state._mm_prompts) else 'Cinematic photorealistic dystopian visual scene, no text.'
        import re as _re_flux_purge
        state._re_flux_purge = _re_flux_purge
        state._act_prompt = state._re_flux_purge.sub('(?i)\\b(?:a\\s+precise\\s+)?graphite\\s+(?:drawing|scene|sketch|illustration)(?:\\s+of\\s+a\\s+man)?\\b[^.]*\\.?', ' ', state._act_prompt)
        state._act_prompt = state._re_flux_purge.sub('(?i)\\bOriginal\\s+scene\\s+concept\\s*:\\s*', '', state._act_prompt)
        state._act_prompt = state._re_flux_purge.sub('\\s{2,}', ' ', state._act_prompt).strip(' ,.')
        state._act_role = state._mm_roles[state._act_i] if state._act_i < len(state._mm_roles) else 'slave'
        state._act_neg = state._mm_negatives[state._act_i] if state._act_i < len(state._mm_negatives) else ''
        state._act_theme = _classify_mei_visual_theme(f'{state.resolved_subject} {state._snippet}')
        state._use_avatar = state._mm_avatar_ref is not None and state._act_i in state._mm_slots and (state._act_role == 'master')
        state._fallback_dest = state._reel_img_dir / f'{state.stem}_act{state._act_i + 1:02d}_fallback.png'
        state._mm_act_meta[state._act_i] = {'snippet': state._snippet, 'theme': state._act_theme, 'role': state._act_role, 'use_avatar': state._use_avatar, 'fallback_dest': state._fallback_dest}
        state._mm_act_jobs[state._act_i] = functools.partial(state.adapter.generate, state._act_prompt, output_stem=f'{state.stem}_act{state._act_i + 1:02d}', output_directory=state._reel_img_dir, reference_image_path=state._mm_avatar_ref if state._use_avatar else None, avatar_mode='ON' if state._use_avatar else 'OFF', reference_image_weight=state._mm_avatar_w if state._use_avatar else None, style_reference_paths=(state._mei_select_srefs(state._mm_style_refs, act_index=state._act_i, episode_seed=state.resolved_subject or '', n_acts=state._seq_n) if state._use_avatar and state._act_i not in (2, state._seq_n - 2) else None) or None, style_reference_weight=state._mm_style_weight if state._use_avatar and state._act_i not in (2, state._seq_n - 2) else None, visual_role=state._act_role, negative_prompt=state._act_neg or None)
    state._mm_gpu_baseline = 0.0
    try:
        from core.remote_gpu_manager import get_manager as _get_rgpu_mm, is_remote_gpu_enabled as _rgpu_on_mm
        state._get_rgpu_mm = _get_rgpu_mm
        state._rgpu_on_mm = _rgpu_on_mm
        if state._rgpu_on_mm('image'):
            state._mm_gpu_baseline = float(getattr(state._get_rgpu_mm().client, 'total_gpu_seconds', 0) or 0)
    except Exception:
        state._mm_gpu_baseline = 0.0
    _apply_still_cache_to_jobs(jobs=state._mm_act_jobs, meta=state._mm_act_meta, n_acts=state._seq_n, start=0, work_dir=state._reel_img_dir, stem=state.stem, extra_dirs=[state.subject_assets] if state.subject_assets else None)
    state._mm_act_results = _run_acts_parallel(state._mm_act_jobs)
    state._mm_ok = 0
    for _act_i in range(0, state._seq_n):
        state._act_i = _act_i
        state._meta = state._mm_act_meta[state._act_i]
        state._snippet = state._meta['snippet']
        state._act_theme = state._meta['theme']
        state._use_avatar = state._meta['use_avatar']
        state._fallback_dest = state._meta['fallback_dest']
        state._cached_still = state._meta.get('cached_path')
        if state._cached_still is not None:
            state._act_img = _ensure_sequence_image(state._cached_still, fallback=state._cached_still, target_path=state._fallback_dest)
            state._sequence_image_paths.append(state._act_img)
            if state._act_i == 0:
                state._act1_base = state._act_img
            _LOG.info('MASTER_MEI sequence | act %d/%d CACHED | avatar=%s theme=%s | beat=%.40s…', state._act_i + 1, state._seq_n, 'ON' if state._use_avatar else 'OFF', state._act_theme, state._snippet)
            continue
        state._result = state._mm_act_results.get(state._act_i)
        if isinstance(state._result, Exception):
            _LOG.warning('MASTER_MEI act %d failed (%s) — blinded fallback.', state._act_i + 1, state._result)
            state._fb = state._act1_base if state._act_i > 0 and state._sequence_image_paths else state.raw_bg_path
            state._sequence_image_paths.append(_ensure_sequence_image(state._fallback_dest, fallback=state._fb, target_path=state._fallback_dest))
            continue
        state._fb = state._sequence_image_paths[0] if state._sequence_image_paths else state.raw_bg_path
        state._act_img = _ensure_sequence_image(state._result, fallback=state._fb, target_path=state._fallback_dest)
        state._sequence_image_paths.append(state._act_img)
        state._mm_ok += 1
        if state._act_i == 0:
            state._act1_base = state._act_img
        _LOG.info('MASTER_MEI sequence | act %d/%d | avatar=%s theme=%s | beat=%.40s…', state._act_i + 1, state._seq_n, 'ON' if state._use_avatar else 'OFF', state._act_theme, state._snippet)
    state._mm_gpu_delta = _track_remote_gpu_batch(state.cost_tracker, seconds_before=state._mm_gpu_baseline, jobs=state._mm_ok)
    if state._mm_ok:
        if state.cost_tracker is not None:
            state.cost_tracker.track_image(model_key='image_flux_schnell', count=state._mm_ok)
        state._images_generated_this_variant += state._mm_ok

def _stage_sequence_render_02(state: VariantState) -> None:
    """Variant worker stage. Original lines 2305-2427."""
    state._act_descriptors = _build_reel_act_descriptors(state.resolved_subject, state._seq_n, page_id=state.page_ctx.page_id or '')
    state._topic_entity_ctx = _extract_topic_visual_entities(state.resolved_subject)
    state._topic_entity_prefix = f'TOPIC ANCHOR: {state._topic_entity_ctx}. ' if state._topic_entity_ctx else ''
    from agents.media.prompt_alignment import build_aligned_visual_block, plan_episode_visual_sequence
    state.build_aligned_visual_block = build_aligned_visual_block
    state.plan_episode_visual_sequence = plan_episode_visual_sequence
    state._ak_visual_plan = state.plan_episode_visual_sequence(state._seq_n, topic=state.resolved_subject, seed=str(state.stem or state.resolved_subject or ''), channel_name=state.page_ctx.page_id or '' if state.page_ctx else '')
    try:
        from agents.rag.channel_rag import get_image_guidance
        state.get_image_guidance = get_image_guidance
        state._ak_rag_image_block = state.get_image_guidance(state.page_ctx.page_id or '' if state.page_ctx else '')
    except Exception:
        state._ak_rag_image_block = ''
    state._parallax_directive = 'DEPTH LAYERS: Compose the frame with distinct foreground, mid-ground and background planes so camera motion creates true parallax separation. Do NOT default to a centered symmetric monument-frame composition unless the spoken beat explicitly requires it — vary spatial arrangement per act. '
    state._lighting_tail = 'TECHNICAL: Ultra-realistic photography, 35mm film grain, full-bleed, no borders, no frames, no captions, no watermarks.'
    state._sr_act_meta = {}
    state._sr_act_jobs = {}
    state._sr_act_start = 0 if state._ak_voice_ready else 1
    state._planned_shots = None
    try:
        from modules.shot_planner.plan import plan_for_reel
        state.plan_for_reel = plan_for_reel
        state._planned_shots = state.plan_for_reel(channel=str((state.page_ctx.page_id if state.page_ctx else '') or ''), topic=str(state.resolved_subject or ''), script=str(state._early_seq_script or ''), tracker=state.cost_tracker)
    except Exception as _plan_exc:
        state._plan_exc = _plan_exc
        _LOG.warning('[ShotPlanner] fell back to resolver: %s', state._plan_exc)
        state._planned_shots = None
    state._reel_checkpoint = None
    state._ladder_on = False
    try:
        from modules.reel_artefact.budget import beat_budget_usd as _beat_usd
        state._beat_usd = _beat_usd
        from modules.reel_artefact.budget import nano_cap as _nano_cap
        state._nano_cap = _nano_cap
        from modules.reel_artefact.budget import reel_budget_usd as _reel_usd
        state._reel_usd = _reel_usd
        from modules.reel_artefact.checkpoint import ReelCheckpoint as _ReelCkpt
        state._ReelCkpt = _ReelCkpt
        from modules.reel_artefact.ladder import rung_names as _rung_names
        state._rung_names = _rung_names
        from modules.reel_artefact.policy import artefact_images_enabled as _ladder_flag
        state._ladder_flag = _ladder_flag
        state._ladder_pages = {part.strip().lower() for part in (os.getenv('REEL_ARTIFACT_IMAGES_CHANNELS') or 'ancient_knowledge').split(',') if part.strip()}
        state._ladder_on = bool(state._ladder_flag()) and str((state.page_ctx.page_id if state.page_ctx else '') or '').strip().lower() in state._ladder_pages
        if state._ladder_on:
            state._reel_checkpoint = state._ReelCkpt.for_reel(str((state.page_ctx.page_id if state.page_ctx else '') or 'ancient_knowledge'), state.stem)
            _LOG.info('LADDER | checkpoint=%s | rungs=%s | beat_usd=%.2f | reel_usd=%.2f | nano_cap=%s', state._reel_checkpoint.path, ','.join(state._rung_names()), state._beat_usd(), state._reel_usd(), state._nano_cap())
    except Exception as _ladder_exc:
        state._ladder_exc = _ladder_exc
        _LOG.warning('LADDER setup skipped (%s)', state._ladder_exc)
        state._ladder_on = False
        state._reel_checkpoint = None
    from modules.reel_grounding.config import grounding_enabled_for as _grounding_for_channel
    state._grounding_for_channel = _grounding_for_channel

def _stage_sequence_render_03(state: VariantState) -> None:
    """Variant worker stage. Original lines 2429-2813."""
    for _act_i in range(state._sr_act_start, state._seq_n):
        state._act_i = _act_i
        state._snippet = state._spoken_snippets[state._act_i] if state._act_i < len(state._spoken_snippets) else state.resolved_subject
        state._prev_snip = state._spoken_snippets[state._act_i - 1] if state._act_i > 0 and state._act_i - 1 < len(state._spoken_snippets) else ''
        state._next_snip = state._spoken_snippets[state._act_i + 1] if state._act_i + 1 < len(state._spoken_snippets) else ''
        state._plan_entry = state._ak_visual_plan[state._act_i] if state._act_i < len(state._ak_visual_plan) else None
        if state._plan_entry:
            state._subject_pair = state._plan_entry['subject']
            state._shot_pair = state._plan_entry['shot']
            state._light_pair = state._plan_entry['lighting']
            state._act_desc = state._subject_pair[1]
        else:
            state._subject_pair = ('(fallback-arc)', '')
            state._shot_pair = None
            state._light_pair = None
            state._act_desc = state._act_descriptors[state._act_i] if state._act_i < len(state._act_descriptors) else f'ACT {state._act_i + 1}: {state.resolved_subject}.'
        state._align = state.build_aligned_visual_block(spoken_snippet=state._snippet, act_index=state._act_i, total_acts=state._seq_n, main_subject=state.resolved_subject, prev_snippet=state._prev_snip, shot_override=state._shot_pair, lighting_override=state._light_pair)
        state._act_neg = None
        state._scene_row = state._audio_first_scenes[state._act_i] if state._act_i < len(state._audio_first_scenes) else None
        if state._scene_row:
            state._snippet = str(state._scene_row.get('spoken_text') or state._snippet)
            state._act_neg = str(state._scene_row.get('negative_prompt') or '') or None
        state._rag_tail = f'\n\n{state._ak_rag_image_block}' if state._ak_rag_image_block else ''
        state._act_banned = list((state._scene_row or {}).get('banned_subjects') or [])
        state._act_anchors = str((state._scene_row or {}).get('domain_anchors') or '')
        if state._scene_row and state._scene_row.get('image_generation_prompt'):
            from agents.media.scene_prompt_generator import sanitize_channel_style as _sanitize_style
            state._sanitize_style = _sanitize_style
            state._safe_style = state._sanitize_style(state._ak_base_style, state._snippet, state._act_banned)
            state._act_prompt = f'{state._scene_row['image_generation_prompt']} {state._safe_style}. {state._parallax_directive}{state._lighting_tail}'
        else:
            state._act_prompt = f'{state._act_desc} {state._topic_entity_prefix}{state._ak_base_style}. {state._align} {state._parallax_directive}{state._lighting_tail}{state._rag_tail}'
        state._plan_shot = None
        if state._planned_shots is not None:
            from modules.shot_planner.plan import shot_for_beat
            state.shot_for_beat = shot_for_beat
            from modules.shot_planner.render import render_shot
            state.render_shot = render_shot
            state._plan_shot = state.shot_for_beat(state._planned_shots, str(state._snippet or ''))
        if state._plan_shot is not None:
            state._rendered = state.render_shot(state._plan_shot, channel=str((state.page_ctx.page_id if state.page_ctx else '') or ''))
            state._act_prompt = state._rendered.positive
            if state._rendered.negative:
                state._act_neg = state._rendered.negative
            state.resolved_subject_for_beat = state._rendered.subject
        elif not state._grounding_for_channel(str((state.page_ctx.page_id if state.page_ctx else '') or '')):
            state.resolved_subject_for_beat = str(state.resolved_subject or '')
        else:
            try:
                from modules.reel_grounding.prompt import assemble_beat
                state.assemble_beat = assemble_beat
                state._shot_name = state._shot_pair[0] if state._shot_pair else ''
                state._shot_text = state._shot_pair[1] if state._shot_pair else ''
                state._light_name = state._light_pair[0] if state._light_pair else ''
                state._light_text = state._light_pair[1] if state._light_pair else ''
                state._beat_plan = state.assemble_beat(topic=str(state.resolved_subject or ''), script=str(state._early_seq_script or ''), spoken=str(state._snippet or ''), draft=state._act_prompt, shot_name=str(state._shot_name or ''), shot_text=str(state._shot_text or ''), lighting_name=str(state._light_name or ''), lighting_text=str(state._light_text or ''), channel=str((state.page_ctx.page_id if state.page_ctx else '') or ''), domain_anchors=str(state._act_anchors or ''), previous_spoken=str(state._prev_snip or ''), next_spoken=str(state._next_snip or ''), banned=list(state._act_banned or []), negative=str(state._act_neg or ''))
                state._act_prompt = state._beat_plan.positive
                state._act_neg = state._beat_plan.negative or state._act_neg
                state._act_banned = list(state._beat_plan.banned)
                if state._beat_plan.object_label:
                    state.resolved_subject_for_beat = state._beat_plan.object_label
                else:
                    state.resolved_subject_for_beat = str(state.resolved_subject or '')
            except Exception as _ground_exc:
                state._ground_exc = _ground_exc
                _LOG.warning('visual grounding skipped (%s)', state._ground_exc)
                state.resolved_subject_for_beat = str(state.resolved_subject or '')
        if state._cta_beat_index is not None and state._act_i == state._cta_beat_index:
            from modules.reel_artefact.route import generic_flux_prompt as _cta_flux
            state._cta_flux = _cta_flux
            state._act_prompt = state._cta_flux(f'Setting: a quiet museum hall or an ancient landscape. {state._snippet}')
            state._plan_shot = None
            state.resolved_subject_for_beat = str(state._snippet or 'closing line')
        try:
            state._subj_name_log = state._subject_pair[0] if state._subject_pair else '(fallback)'
            state._shot_name_log = state._shot_pair[0] if state._shot_pair else '(fallback-cycle)'
            state._light_name_log = state._light_pair[0] if state._light_pair else '(none)'
            _LOG.info('VISUAL PLAN | act %d/%d | via=%s | object=%s | subject=%s | shot=%s | lighting=%s', state._act_i + 1, state._seq_n, 'planner' if state._plan_shot is not None else 'resolver', state.resolved_subject_for_beat, state._subj_name_log, state._shot_name_log, state._light_name_log)
        except Exception:
            pass
        state._fallback_dest = state._reel_img_dir / f'{state.stem}_act{state._act_i + 1:02d}_fallback.png'
        state._act_stem = f'{state.stem}_act{state._act_i + 1:02d}_r01'
        state._sr_act_meta[state._act_i] = {'snippet': state._snippet, 'fallback_dest': state._fallback_dest, 'prompt': state._act_prompt, 'stem': f'{state.stem}_act{state._act_i + 1:02d}', 'banned_subjects': list(state._act_banned or []), 'domain_anchors': str(state._act_anchors or ''), 'negative_prompt': state._act_neg, 'episode_subject': str(state.resolved_subject_for_beat or '')}
        state._gen = state.adapter.generate
        state._art_kwargs = {}
        if state._plan_shot is not None:
            try:
                from modules.reel_artefact.hook import wrap_reel_generate
                state.wrap_reel_generate = wrap_reel_generate
                state._gen, state._art_kwargs = state.wrap_reel_generate(state.adapter.generate, channel=str((state.page_ctx.page_id if state.page_ctx else '') or ''), shot=state._plan_shot)
            except Exception as _art_exc:
                state._art_exc = _art_exc
                _LOG.warning('artefact still hook skipped (%s)', state._art_exc)
        state._sr_act_meta[state._act_i]['generate_fn'] = state._gen
        state._sr_act_meta[state._act_i]['artefact_kwargs'] = dict(state._art_kwargs)
        state._sr_act_meta[state._act_i]['is_cta'] = bool(state._cta_beat_index is not None and state._act_i == state._cta_beat_index)
        from modules.reel_visual_qa import generate_and_gate as _qwen_gate
        state._qwen_gate = _qwen_gate
        state._doc_role = 'documentary' if (state.page_ctx.page_id or '').lower() != 'master_mei' else None
        if state._asset_assignments and state._act_i < len(state._asset_assignments):
            from modules.reel_artefact.asset_pool import make_pool_job as _make_pool_job
            state._make_pool_job = _make_pool_job
            if state._act_i == state._sr_act_start:
                _LOG.info('ASSET POOL | stills from the pool | ladder rounds skipped')
            state._sr_act_jobs[state._act_i] = state._make_pool_job(state._asset_assignments[state._act_i], state._reel_img_dir / f'{state.stem}_act{state._act_i + 1:02d}.jpg', list(state._asset_pool), state.adapter.generate, state._pool_geography, state._act_i)
        elif state._ladder_on:
            from modules.reel_artefact.orchestrate import make_climb_job as _make_climb_job
            state._make_climb_job = _make_climb_job
            state._sr_act_jobs[state._act_i] = state._make_climb_job(beat_index=state._act_i, narration=state._snippet, prompt=state._act_prompt, output_directory=state._reel_img_dir, stem=f'{state.stem}_act{state._act_i + 1:02d}', channel=str((state.page_ctx.page_id if state.page_ctx else '') or 'ancient_knowledge'), flux_generate=state.adapter.generate, shot=state._plan_shot, checkpoint=state._reel_checkpoint, generate_kwargs={'negative_prompt': state._act_neg, 'visual_role': state._doc_role}, episode_subject=str(state.resolved_subject or state.resolved_subject_for_beat or ''), banned_subjects=list(state._act_banned or []) or None, is_cta=bool(state._sr_act_meta[state._act_i]['is_cta']), n_beats=int(state._seq_n))
        else:
            state._sr_act_jobs[state._act_i] = functools.partial(state._qwen_gate, state._gen, prompt=state._act_prompt, chunk_text=state._snippet, output_stem=state._act_stem, output_directory=state._reel_img_dir, channel=state.page_ctx.page_id or 'ancient_knowledge', search_dirs=[state._reel_img_dir, *([state.subject_assets] if state.subject_assets else [])], generate_kwargs={'avatar_mode': 'OFF', 'visual_role': state._doc_role, 'negative_prompt': state._act_neg, 'free_image_sentence': state._snippet, 'free_image_entity': str(state.resolved_subject_for_beat or state.resolved_subject or ''), **state._art_kwargs}, banned_subjects=list(state._act_banned or []) or None, domain_anchors=str(state._act_anchors or ''), prefer_stem=state.stem, episode_subject=str(state.resolved_subject_for_beat or state.resolved_subject or ''))
    state._gpu_baseline = 0.0
    try:
        from core.remote_gpu_manager import get_manager as _get_rgpu, is_remote_gpu_enabled as _rgpu_on
        state._get_rgpu = _get_rgpu
        state._rgpu_on = _rgpu_on
        if state._rgpu_on('image'):
            state._gpu_baseline = float(getattr(state._get_rgpu().client, 'total_gpu_seconds', 0) or 0)
    except Exception:
        state._gpu_baseline = 0.0
    _apply_still_cache_to_jobs(jobs=state._sr_act_jobs, meta=state._sr_act_meta, n_acts=state._seq_n, start=state._sr_act_start, work_dir=state._reel_img_dir, stem=state.stem, extra_dirs=[state.subject_assets] if state.subject_assets else None, spoken_by_act={i: str(state._sr_act_meta[i].get('snippet') or '') for i in state._sr_act_meta})
    if state._early_music_thread is None and state.page_ctx and app_config.ELEVENLABS_API_KEY:
        state._clips_early = Path(app_config.PAGE_OUTPUTS_DIR) / 'clips'
        state._clips_early.mkdir(parents=True, exist_ok=True)
        state._use_v2_early = bool(getattr(state.page_ctx, 'use_music_v2_bed', False))
        state._style_early = state.page_ctx.ambient_music_style or 'mystery'
        state._dur_early = float(state.page_ctx.reel_duration or 105.0)
        state._stem_early = f'{state.stem}_v{state.variant + 1:02d}'
        state._topic_early = str(state.resolved_subject or '')
        state._directive_early = state.page_ctx.music_prompt_directive_path
        state._channel_early = state.page_ctx.page_id or ''
        state._sfx_prompt_early = state.page_ctx.ambient_sfx_prompt or None

        def _early_music() -> None:
            try:
                if state._use_v2_early:
                    _min = 40.0
                    try:
                        _cfg = getattr(state.page_ctx, 'page_cfg', None) or {}
                        _min = float(_cfg.get('MUSIC_V2_MIN_SECONDS', 40.0) or 40.0)
                    except Exception:
                        _min = 40.0
                    bed, _impact = generate_master_mei_soundscape(state._clips_early, stem=state._stem_early, duration_seconds=max(_min, state._dur_early), include_impact_sfx=False, topic=state._topic_early, directive_path=state._directive_early, style_profile=state._style_early, channel_name=state._channel_early)
                    sfx = generate_ambient_track(state._clips_early / f'{state._stem_early}_atmosphere_sfx.mp3', duration_seconds=10.0, prompt=state._sfx_prompt_early)
                    state._early_music_box['bed'] = bed
                    state._early_music_box['sfx'] = sfx
                    state._early_music_box['mode'] = 'v2'
                else:
                    ambient = generate_ambient_track(state._clips_early / f'{state._stem_early}_ambient.mp3', duration_seconds=min(12.0, state._dur_early), prompt=state._sfx_prompt_early)
                    state._early_music_box['ambient'] = ambient
                    state._early_music_box['mode'] = 'ambient'
            except Exception as _music_exc:
                _LOG.warning('early music bed failed (%s)', _music_exc)
                state._early_music_box['mode'] = 'failed'
        state._early_music_thread = threading.Thread(target=_early_music, name='reel-music-bed', daemon=True)
        state._early_music_thread.start()
        _LOG.info('ECONOMIC_REEL | music bed started during image judging | mode=%s', 'v2' if state._use_v2_early else 'ambient')
    if state._ladder_on:
        from modules.reel_artefact.orchestrate import run_reel_stills as _run_reel_stills
        state._run_reel_stills = _run_reel_stills
        state._sr_act_results = state._run_reel_stills(state._sr_act_jobs)
    else:
        state._sr_act_results = _run_acts_parallel(state._sr_act_jobs)
    state._ok_acts = 0

def _stage_sequence_render_04(state: VariantState) -> None:
    """Variant worker stage. Original lines 2814-3133."""
    from modules.reel_qa.models import StillGateResult
    state.StillGateResult = StillGateResult
    from modules.reel_visual_qa import generate_and_gate as _qwen_gate
    state._qwen_gate = _qwen_gate

    def _accept_gate(_gate: state.StillGateResult, _idx: int, _text: str) -> None:
        if _gate.extra_generations:
            state._images_generated_this_variant += int(_gate.extra_generations)
        _verdict_row = dict(_gate.verdict or {})
        try:
            if _verdict_row.get('resumed'):
                pass
            elif _verdict_row.get('ladder'):
                for _spend_key, _spend_n in dict(_verdict_row.get('spend') or {}).items():
                    if int(_spend_n) > 0:
                        state._image_bill[_spend_key] = state._image_bill.get(_spend_key, 0) + int(_spend_n)
            else:
                from modules.reel_artefact.pricing import generation_bill as _generation_bill
                _bill_key, _bill_n = _generation_bill(_gate.path, int(_gate.extra_generations or 0), attempts=len(_gate.attempts or []))
                if _bill_key and _bill_n > 0:
                    state._image_bill[_bill_key] = state._image_bill.get(_bill_key, 0) + int(_bill_n)
        except Exception:
            pass
        _row = _gate.beat_row(index=_idx, text=_text)
        _meta_row = state._sr_act_meta.get(_idx) or {}
        _row['prompt'] = str(_meta_row.get('prompt') or '')
        _row['is_cta'] = bool(_meta_row.get('is_cta'))
        try:
            from modules.reel_artefact.source import source_meta as _source_meta
            _src = _source_meta(_row.get('image_path'))
            _row['reference_image'] = str(_src.get('reference_image') or '')
            _row['canonical_shape'] = str(_src.get('canonical_shape') or '')
            _row['image_provider'] = str(_src.get('provider') or '')
        except Exception:
            pass
        state._beat_qa_rows.append(_row)
        if _gate.publishable and (not _verdict_row.get('ladder')) and str(_row.get('image_provider') or '').strip():
            try:
                from modules.reel_artefact.logbook import note_accepted as _note_accepted
                _note_accepted(str(_row.get('image_provider') or ''))
            except Exception:
                pass
        if _gate.publishable and _gate.path is not None and Path(_gate.path).is_file():
            try:
                from modules.reel_grounding.still_cache import write_spoken_sidecar
                _score = (_gate.verdict or {}).get('relevance_score')
                write_spoken_sidecar(_gate.path, _text, passed=True, score=float(_score) if _score is not None else None)
            except Exception:
                pass
            state._sequence_image_paths.append(Path(_gate.path))
            state._ok_acts += 1
            try:
                from modules.reel_artefact.policy import artefact_images_enabled
                from modules.reel_artefact.reuse import remember_approved
                _passed_judge = bool(_verdict_row.get('is_relevant'))
                if state._ladder_on and artefact_images_enabled() and _passed_judge and (not _verdict_row.get('kept')):
                    remember_approved(_gate.path, kind=str(_verdict_row.get('beat_kind') or 'object'))
                    if state._reel_checkpoint is not None and (not _verdict_row.get('resumed')):
                        state._reel_checkpoint.save_beat(_idx, image=_gate.path, score=_verdict_row.get('relevance_score'), reason=str(_verdict_row.get('rejection_reason') or ''), rung=str(_verdict_row.get('rung') or ''), verdict=_verdict_row, first_pass=_gate.first_pass, kind=str(_verdict_row.get('beat_kind') or 'object'))
            except Exception:
                pass
            _LOG.info('Sequence reel | act %d/%d QA pass | beat=%.48s…', _idx + 1, state._seq_n, _text)
            return
        _reused = None
        try:
            from modules.reel_artefact.policy import artefact_images_enabled
            from modules.reel_artefact.reuse import reuse_into
            if state._ladder_on and artefact_images_enabled() and (not _verdict_row.get('ladder')):
                _meta_row_reuse = state._sr_act_meta.get(_idx) or {}
                _entity_id = str(_meta_row_reuse.get('artefact_kwargs', {}).get('artefact_entity_id', '') or '')
                _reuse_dest = state._reel_img_dir / f'{state.stem}_act{_idx + 1:02d}_reuse.jpg'
                _reused = reuse_into(_reuse_dest, _idx, entity_id=_entity_id)
        except Exception:
            _reused = None
        if _reused is not None and Path(_reused).is_file():
            _row['image_path'] = str(_reused)
            _row['passed'] = True
            _row['reuse'] = 'approved'
            state._sequence_image_paths.append(Path(_reused))
            state._ok_acts += 1
            _LOG.warning('REEL QA REUSE | act %d/%d reason=%s — approved still kept the reel', _idx + 1, state._seq_n, _gate.blocked_reason or 'rejected')
            return
        _guaranteed = None
        try:
            from modules.reel_artefact.reuse import guarantee_still
            _meta_fallback = state._sr_act_meta.get(_idx) or {}
            _entity_fallback = str(_meta_fallback.get('artefact_kwargs', {}).get('artefact_entity_id', '') or '')
            _fallback_dest = state._reel_img_dir / f'{state.stem}_act{_idx + 1:02d}_fallback.jpg'
            _guaranteed = guarantee_still(_fallback_dest, _idx, entity_id=_entity_fallback, n_beats=state._seq_n)
        except Exception as _fall_exc:
            _LOG.warning('REEL QA FALLBACK failed (%s)', _fall_exc)
            _guaranteed = None
        if _guaranteed is not None and Path(_guaranteed).is_file():
            _row['image_path'] = str(_guaranteed)
            _row['passed'] = True
            _row['reuse'] = 'fallback'
            state._sequence_image_paths.append(Path(_guaranteed))
            state._ok_acts += 1
            _LOG.warning('REEL QA FALLBACK | act %d/%d reason=%s — still kept, reel continues', _idx + 1, state._seq_n, _gate.blocked_reason or 'rejected')
            return
        state._reel_qa_block_compile = True
        _LOG.error('REEL QA BLOCK | act %d/%d reason=%s — no substitute still', _idx + 1, state._seq_n, _gate.blocked_reason or 'rejected')
    for _act_i in range(state._sr_act_start, state._seq_n):
        state._act_i = _act_i
        state._meta = state._sr_act_meta[state._act_i]
        state._snippet = state._meta['snippet']
        state._cached_still = state._meta.get('cached_path')
        if state._cached_still is not None:
            from modules.reel_qa.relevance import evaluate_chunk_relevance as _score_still
            state._score_still = _score_still
            from modules.reel_qa.relevance import evaluate_with_technical_retry as _score_retry
            state._score_retry = _score_retry
            state._cached_verdict = state._score_retry(state._score_still, state._cached_still, state._snippet, str(state._meta.get('prompt') or ''), banned_subjects=list(state._meta.get('banned_subjects') or []) or None)
            from modules.reel_qa.relevance import verdict_passed as _verdict_passed
            state._verdict_passed = _verdict_passed
            if state._verdict_passed(state._cached_verdict) and state._cached_verdict.get('qa_status') != 'unavailable' and Path(state._cached_still).is_file():
                _accept_gate(state.StillGateResult(path=Path(state._cached_still), extra_generations=0, publishable=True, attempts=[state._cached_verdict], verdict=state._cached_verdict), state._act_i, state._snippet)
                continue
            if state._cached_verdict.get('qa_status') == 'unavailable':
                _accept_gate(state.StillGateResult(path=None, extra_generations=0, publishable=False, blocked_reason=str(state._cached_verdict.get('rejection_reason') or 'qa_unavailable'), attempts=[state._cached_verdict], verdict=state._cached_verdict), state._act_i, state._snippet)
                continue
            if state._ladder_on:
                state._cached_miss = state._sr_act_results.get(state._act_i)
                if isinstance(state._cached_miss, state.StillGateResult):
                    _accept_gate(state._cached_miss, state._act_i, state._snippet)
                    continue
            state._regen = state._qwen_gate(state._meta.get('generate_fn') or state.adapter.generate, prompt=str(state._meta.get('prompt') or state._snippet), chunk_text=state._snippet, output_stem=f'{state._meta.get('stem') or 'act'}_r01', output_directory=state._reel_img_dir, channel=state.page_ctx.page_id or 'ancient_knowledge', generate_kwargs={'avatar_mode': 'OFF', 'negative_prompt': state._meta.get('negative_prompt'), 'free_image_sentence': state._snippet, 'free_image_entity': str(state._meta.get('episode_subject') or state.resolved_subject or ''), **dict(state._meta.get('artefact_kwargs') or {})}, banned_subjects=list(state._meta.get('banned_subjects') or []) or None, domain_anchors=str(state._meta.get('domain_anchors') or ''), episode_subject=str(state._meta.get('episode_subject') or state.resolved_subject or ''))
            _accept_gate(state._regen, state._act_i, state._snippet)
            continue
        state._result = state._sr_act_results.get(state._act_i)
        if isinstance(state._result, Exception):
            _accept_gate(state.StillGateResult(path=None, extra_generations=0, publishable=False, blocked_reason=f'generate_failed:{type(state._result).__name__}'), state._act_i, state._snippet)
            continue
        if isinstance(state._result, state.StillGateResult):
            _accept_gate(state._result, state._act_i, state._snippet)
            continue
        _accept_gate(state.StillGateResult(path=None, extra_generations=0, publishable=False, blocked_reason='qa_result_missing'), state._act_i, state._snippet)
    state._gpu_delta = _track_remote_gpu_batch(state.cost_tracker, seconds_before=state._gpu_baseline, jobs=state._ok_acts)
    if state.cost_tracker is not None:
        try:
            from modules.reel_artefact.policy import artefact_images_enabled
            state.artefact_images_enabled = artefact_images_enabled
            if state.artefact_images_enabled() and state._image_bill and (state._ladder_on or state._asset_pool):
                for _bill_key, _bill_n in state._image_bill.items():
                    state._bill_key = _bill_key
                    state._bill_n = _bill_n
                    if int(state._bill_n) > 0:
                        state.cost_tracker.track_image(model_key=state._bill_key, count=int(state._bill_n))
            elif state._ok_acts and (not state._asset_pool):
                state.cost_tracker.track_image(model_key='image_flux_schnell', count=state._ok_acts)
        except Exception:
            if state._ok_acts and (not state._asset_pool):
                state.cost_tracker.track_image(model_key='image_flux_schnell', count=state._ok_acts)
    if state._ok_acts:
        state._images_generated_this_variant += state._ok_acts
    _LOG.info('REEL VisualQA | still gate applied | blocked=%s beats=%d frames=%d', state._reel_qa_block_compile, len(state._beat_qa_rows), len(state._sequence_image_paths))
    try:
        from modules.reel_artefact.logbook import summary_line as _artefact_summary
        state._artefact_summary = _artefact_summary
        from modules.reel_artefact.policy import artefact_images_enabled as _art_on
        state._art_on = _art_on
        if state._art_on():
            _LOG.info(state._artefact_summary())
    except Exception:
        pass
