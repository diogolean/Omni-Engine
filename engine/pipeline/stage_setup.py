# -*- coding: utf-8 -*-
"""Topic, batch angle, sketch lock, and horror override."""
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

from engine.pipeline.stage_render_still import _stage_render_still_01, _stage_render_still_03
from engine.pipeline.stage_sequence_plan import _stage_sequence_plan_01, _stage_sequence_plan_02, _stage_sequence_plan_03

def _stage_setup_01(state: VariantState) -> None:
    """Variant worker stage. Original lines 146-516."""
    state.stem = f'{state.slug}_v{state.variant + 1:02d}'
    state.variation_index = state.variant
    state._wan_t0_variant = time.monotonic()
    state._wan_stage_times = {}
    state._wan_act_status = []
    state.batch_cost_tracker = state.cost_tracker
    if state.batch_cost_tracker is not None:
        state.cost_tracker = CostTracker(page_id=state.batch_cost_tracker.page_id, cost_tier=state.batch_cost_tracker.cost_tier)
    state._batch_angle = None
    state._batch_angle_block = ''
    state._visual_angle_focus = ''
    if state.batch_angles and state.variant < len(state.batch_angles):
        state._batch_angle = state.batch_angles[state.variant]
        state._batch_angle_block = state._batch_angle.prompt_block()
        state._visual_angle_focus = (state._batch_angle.visual_focus or '').strip()
        state.resolved_subject = state._batch_angle.combined_topic
        state.slug = subject_slug(state.resolved_subject)
        state.stem = f'{state.slug}_v{state.variant + 1:02d}'
        state.subject_assets = app_config.ASSETS_DIR / state.slug
        state.subject_assets.mkdir(parents=True, exist_ok=True)
        _LOG.info('Variant %d/%d | BATCH ANGLE → %r | hook_style=%s | global=%r', state.variant + 1, state._batch_angle.total, state._batch_angle.angle_title, state._batch_angle.hook_style, state._batch_angle.global_topic)
    elif state.per_variant_topics and state.variant < len(state.per_variant_topics):
        state._vt = state.per_variant_topics[state.variant]
        if state._vt and state._vt != state.resolved_subject:
            state.resolved_subject = state._vt
            state.slug = subject_slug(state.resolved_subject)
            state.stem = f'{state.slug}_v{state.variant + 1:02d}'
            state.subject_assets = app_config.ASSETS_DIR / state.slug
            state.subject_assets.mkdir(parents=True, exist_ok=True)
            _LOG.info('Variant %d | per-variant topic override → %r', state.variant + 1, state.resolved_subject)
    if state.page_ctx and (state.page_ctx.page_id or '').lower() == 'ancient_knowledge':
        from modules.reel_artefact.provider_health import preflight_text as _preflight_text
        state._preflight_text = _preflight_text
        state._preflight_text('ancient_knowledge')
    state.cta_kw = contextual_cta_keyword(state.resolved_subject)
    state.humanizer_notes = state.bm.humanizer_summary
    state.caption = '(skipped)'
    state.raw_sheet = '(skipped)'
    state.img_path_display = '(skipped)'
    state.caption_mode_tag = None
    state.durable_abs = None
    state.planner_row_ix = None
    state.video_path_str = ''
    state.overlay_text = ''
    state.visual_subject = ''
    state._seo_title_local = ''
    if state.post_type in ('SMART_BAIT', 'ECONOMIC_REEL', 'WAN_REEL', 'CAROUSEL') and (not state.skip_caption):
        assert state.caption_engine is not None
        try:
            state._bait_examples = _load_engagement_bait_examples(state.page_ctx) if state.page_ctx and (state.page_ctx.page_id or '').lower() == 'wonder_feed' else ''
            state._hooks_snapshot = list(state.generated_hooks_cache or [])
            state._reject_note = ''
            for _uniq_attempt in range(1, MAX_UNIQUENESS_RETRIES + 1):
                state._uniq_attempt = _uniq_attempt
                state.overlay_text, state.caption, state.caption_mode_tag, state.visual_subject = state.caption_engine.humanize_smart_bait(state.resolved_subject, page_display_name=state.page_ctx.display_name if state.page_ctx else '', page_niche=state.page_ctx.content_niche if state.page_ctx else '', cta_enabled=state.cta_enabled, economic=state.economic, model_id=state.econ_model if state.economic else None, post_type=state.post_type, engagement_bait_examples=state._bait_examples, previously_generated_hooks=state._hooks_snapshot, niche_disclaimer=state.page_ctx.niche_disclaimer if state.page_ctx else '', narrative_mode=state.page_ctx.narrative_mode if state.page_ctx else '', batch_angle_block=state._batch_angle_block, uniqueness_rejection=state._reject_note, long_format=state.post_type == 'CAROUSEL', forbid_signature=state.post_type == 'CAROUSEL')
                state._seo_title_local = (getattr(state.caption_engine, 'last_seo_title', '') or '').strip()
                if state.uniqueness_guard is None:
                    break
                state._desc_first = (state.caption or '').strip().splitlines()[0] if state.caption else ''
                state._ok, state._prior, state._score = state.uniqueness_guard.try_claim(state._seo_title_local, state.overlay_text, state._desc_first)
                if state._ok:
                    _LOG.info('Batch uniqueness OK | variant=%d attempt=%d title=%r', state.variant + 1, state._uniq_attempt, (state._seo_title_local or state.overlay_text)[:80])
                    break
                state._reject_note = state.uniqueness_guard.rejection_instruction(state._prior or '', state._score)
                _LOG.warning('Batch uniqueness REJECT | variant=%d attempt=%d sim=%.0f%% prior=%r', state.variant + 1, state._uniq_attempt, state._score * 100, (state._prior or '')[:80])
                if state._uniq_attempt >= MAX_UNIQUENESS_RETRIES:
                    _LOG.error('Batch uniqueness FAILED after %d attempts | variant=%d — proceeding with last draft (may be similar).', MAX_UNIQUENESS_RETRIES, state.variant + 1)
                    state.uniqueness_guard.register(state._seo_title_local, state.overlay_text, state._desc_first)
                if state._batch_angle is not None and state._visual_angle_focus:
                    state.visual_subject = f'{state._visual_angle_focus}. {state.visual_subject or state.resolved_subject}'.strip()
            if state._visual_angle_focus and state.visual_subject:
                if state._visual_angle_focus.lower() not in state.visual_subject.lower():
                    state.visual_subject = f'{state._visual_angle_focus}. {state.visual_subject}'
            elif state._visual_angle_focus and (not state.visual_subject):
                state.visual_subject = state._visual_angle_focus
        except Exception as exc:
            state.exc = exc
            _LOG.error('Smart bait generation failed variant %s: %s', state.variant + 1, state.exc, exc_info=True)
            logging.error('VARIANT_FAIL | smart_bait | variant=%s | err=%s', state.variant + 1, state.exc)
            raise
        if state.overlay_text and state.hooks_cache_lock is not None and (state.generated_hooks_cache is not None):
            with state.hooks_cache_lock:
                if state.overlay_text not in state.generated_hooks_cache:
                    state.generated_hooks_cache.append(state.overlay_text)
                    if state.hooks_cache_path is not None:
                        try:
                            import json as _jw
                            state._jw = _jw
                            state._to_save = state.generated_hooks_cache[-60:]
                            state.hooks_cache_path.parent.mkdir(parents=True, exist_ok=True)
                            state.hooks_cache_path.write_text(state._jw.dumps(state._to_save, ensure_ascii=False, indent=2), encoding='utf-8')
                            try:
                                from modules.durable_store import sync_state_file
                                state.sync_state_file = sync_state_file
                                state.sync_state_file(state.hooks_cache_path)
                            except Exception:
                                pass
                        except Exception as _hwe:
                            state._hwe = _hwe
                            _LOG.warning('Could not persist hooks cache (%s).', state._hwe)
        if state.caption_mode_tag == 'researcher_fallback' or (not state.overlay_text and (not state.caption)):
            _LOG.warning("Smart bait generation yielded no usable content for variant %s of '%s'.", state.variant + 1, state.resolved_subject)
        if state.post_type == 'CAROUSEL':
            state._pid = (state.page_ctx.page_id if state.page_ctx else '').lower()
            state.visual_subject = ground_image_concept(state.visual_subject or state.resolved_subject, page_id=state._pid) or state.visual_subject
    elif state.post_type == 'LONG_CAPTION_IMAGE' and (not state.skip_caption):
        assert state.caption_engine is not None
        try:
            state.caption, state.caption_mode_tag = state.caption_engine.humanize_long_caption(state.resolved_subject, page_display_name=state.page_ctx.display_name if state.page_ctx else '', page_niche=state.page_ctx.content_niche if state.page_ctx else '', cta_enabled=state.cta_enabled, cta_keyword=state.cta_kw if state.cta_enabled else None, economic=state.economic, model_id=state.econ_model if state.economic else None, signature=state.page_ctx.caption_signature if state.page_ctx else '')
        except Exception as exc:
            state.exc = exc
            _LOG.error('Long caption generation failed variant %s: %s', state.variant + 1, state.exc, exc_info=True)
            logging.error('VARIANT_FAIL | long_caption | variant=%s | err=%s', state.variant + 1, state.exc)
            raise
        if not state.caption:
            _LOG.warning("Long caption generation yielded no content for variant %s of '%s'.", state.variant + 1, state.resolved_subject)
    state._is_master_mei_page = bool(state.page_ctx and (state.page_ctx.page_id or '').lower() == 'master_mei')
    state._graphite_locked = not state._is_master_mei_page and state.post_type in ('SMART_BAIT', 'LONG_CAPTION_IMAGE', 'ECONOMIC_REEL', 'WAN_REEL', 'CAROUSEL') and (bool(state.page_ctx and (state.page_ctx.base_graphite_prompt or state.page_ctx.sketch_style_prompt)) or state.image_style == 'SKETCH')
    if state._graphite_locked:
        _LOG.debug('%s | Graphite style lock active (image_style=%s). Pipeline: BASE_GRAPHITE_PROMPT → build_smart_bait_image_prompt().', state.post_type, state.image_style)
    state.effective_atmosphere = state.atmosphere_style
    state._mutated_subject = state.resolved_subject or ''
    if not state._is_master_mei_page and state.post_type in ('SMART_BAIT', 'ECONOMIC_REEL', 'WAN_REEL') and state.overlay_text and (state.caption_engine is not None):
        state.scene = state.visual_subject or state.caption_engine.extract_smart_bait_image_theme(state.overlay_text, economic=state.economic)
        if state.scene:
            state._base_prompt = state.page_ctx.base_graphite_prompt or state.page_ctx.sketch_style_prompt if state.page_ctx else ''
            if not state._base_prompt and state.image_style == 'SKETCH':
                state._base_prompt = 'in the style of a detailed, emotional charcoal pencil sketch illustration, monochrome cross-hatching, moody atmosphere, dark vignette, soft horror style couple with expressionless porcelain masks'
            state.scene = maybe_inject_horror_mutation(state.scene, probability=0.35)
            state.effective_atmosphere = build_smart_bait_image_prompt(state.scene, state._base_prompt)
            _LOG.debug('%s | scene (post-mutation): %s', state.post_type, state.scene[:120])
    elif state.post_type == 'LONG_CAPTION_IMAGE':
        state._base_prompt = state.page_ctx.base_graphite_prompt or state.page_ctx.sketch_style_prompt if state.page_ctx else ''
        if not state._base_prompt and state.image_style == 'SKETCH':
            state._base_prompt = 'in the style of a detailed, emotional charcoal pencil sketch illustration, monochrome cross-hatching, moody atmosphere, dark vignette, soft horror style couple with expressionless porcelain masks'
        if state._base_prompt:
            state._mutated_subject = maybe_inject_horror_mutation(state.resolved_subject, probability=0.35)
            state.effective_atmosphere = build_smart_bait_image_prompt(state._mutated_subject, state._base_prompt)
            _LOG.debug('LONG_CAPTION_IMAGE | scene (post-mutation): %s', (state._mutated_subject or '')[:120])
    if state._visual_angle_focus:
        state._vf = state._visual_angle_focus.rstrip(' .')
        if state._vf.lower() not in (state.effective_atmosphere or '').lower():
            state.effective_atmosphere = f'{state.effective_atmosphere.rstrip(' .')}. BATCH ANGLE VISUAL FOCUS (unique to this video): {state._vf}.'.strip()
            _LOG.info('Variant %d | visual angle injected → %r', state.variant + 1, state._vf[:80])
    if state.post_type == 'SMART_BAIT':
        state.image_avatar_mode = 'OFF'
    elif state.post_type in ('LONG_CAPTION_IMAGE', 'CTA_CAPTION_IMAGE', 'CAROUSEL'):
        state._cli_av = str((state.page_ctx.page_cfg.get('AVATAR_CLI') if state.page_ctx else None) or '').upper().strip()
        state.image_avatar_mode = state._cli_av if state._cli_av in ('ON', 'OFF') else 'OFF'
    elif state.post_type in ('ECONOMIC_REEL', 'WAN_REEL') and state.page_ctx and ((state.page_ctx.page_id or '').lower() == 'master_mei') and (not state.page_ctx.sequence_force_avatar_off):
        state.image_avatar_mode = 'ON'
    elif state.post_type in ('ECONOMIC_REEL', 'WAN_REEL'):
        state.image_avatar_mode = 'OFF'
    else:
        state.image_avatar_mode = state.avatar_mode
    state._SKETCH_LOCK_SUFFIX = ' in the style of a detailed, emotional charcoal pencil sketch illustration, monochrome cross-hatching, moody atmosphere'
    if state._graphite_locked and state.effective_atmosphere and (state._SKETCH_LOCK_SUFFIX.strip() not in state.effective_atmosphere):
        state.effective_atmosphere = state.effective_atmosphere.rstrip(' .') + state._SKETCH_LOCK_SUFFIX
        _LOG.debug('SKETCH LOCK | enforced sketch suffix on effective_atmosphere.')

def _stage_setup_02(state: VariantState) -> None:
    """Variant worker stage. Original lines 526-3266."""
    if state.page_ctx and (state.page_ctx.page_id or '').lower() == 'wonder_feed':
        state._il = state.page_ctx.illustration_style.rstrip(' .') if state.page_ctx.use_style_reference and state.page_ctx.illustration_style else 'Highly detailed pencil sketch style illustration, but restricted to only 3 colors: dark azure blue, black, and white. Dramatic lighting, expressive. Clean, minimalist, no messy details.'
        state._chr = state.page_ctx.style_characters.rstrip(' .') if state.page_ctx.use_style_reference and state.page_ctx.style_characters else 'A realistic man and a woman in an intense, dramatic relationship dynamic. They are the consistent recurring personas.'
        state._WF_SOFT_HORROR_VARIANTS = [f'{state._il}. {state._chr}. The man wears a completely plain, expressionless white porcelain-like mask over his face, while the woman exhibits an intense, raw, anxious expression. Full-bleed, edge-to-edge canvas portrait, no borders, no bookshelves, no desks, no notebooks.', f'{state._il}. {state._chr}. The woman is tethered by a dark, fragmented shadow to a looming, faceless male silhouette fading into subtle ash particles. Full-bleed, full-screen canvas portrait, no ambient furniture, no clutter.', f'{state._il}. {state._chr}. The man stands with an eerie, featureless white mask in a heavy vignette shadow, while the woman stares blankly at a shattered mirror pool on the floor reflecting her anxiety. Edge-to-edge full bleed, zero margins, no generic room elements.', f"{state._il}. {state._chr}. The man's silhouette is partially dissolving into fine charcoal dust at the edges while the woman reaches toward him with trembling hands. Full-screen portrait, deep vignette shadows, no domestic objects or interior framing.", f"{state._il}. {state._chr}. Both figures are in close emotional proximity but the man's face is hidden behind a smooth white porcelain half-mask; the woman's expression is raw and unguarded. Full-bleed graphite canvas, no borders, no furniture."]
        import random as _rnd_var
        state._rnd_var = _rnd_var
        state._WF_SKETCH_PREFIX = state._rnd_var.choice(state._WF_SOFT_HORROR_VARIANTS) + ' '
        state._PHOTO_TERMS = ['leather armchair', 'armchair', 'library', 'bookshelf', 'coffee cup', 'notebook', 'pen', 'wooden table', 'wooden desk', 'cup of tea', 'dried flowers', 'earthenware', 'fairy lights', 'terracotta', 'blush pink', 'warm cream', '35mm film', 'linen curtains', 'journaling', 'emotionally safe', 'aesthetically cozy', 'cozy', 'lifestyle photography', 'morning light', 'clean surface', 'window seat', 'blanket', 'cozy room', 'sketchbook', 'page', 'borders', 'desk', 'table', 'CINEMATIC ENVIRONMENTAL PHOTOGRAPHY', 'NO HUMAN SUBJECTS']
        state._clean = state.effective_atmosphere
        for _pt in state._PHOTO_TERMS:
            state._pt = _pt
            state._lo = state._clean.lower()
            state._idx = state._lo.find(state._pt.lower())
            while state._idx != -1:
                state._clean = state._clean[:state._idx] + state._clean[state._idx + len(state._pt):]
                state._lo = state._clean.lower()
                state._idx = state._lo.find(state._pt.lower())
        import re as _re_mod
        state._re_mod = _re_mod
        state._clean = state._re_mod.sub('[ \\t]{2,}', ' ', state._clean).strip(' ,.')
        state.effective_atmosphere = state._WF_SKETCH_PREFIX + state._clean
        _LOG.info('wonder_feed PROMPT LOCK | use_style_ref=%s | compiled: %s', state.page_ctx.use_style_reference, state.effective_atmosphere[:160])
    state._mm_image_prompt_override = ''
    state._isolated_channel = ChannelFactory.is_isolated((state.page_ctx.page_id or '').lower() if state.page_ctx else '')
    if state.page_ctx and (state.page_ctx.page_id or '').lower() == 'master_mei' and (not state._isolated_channel):
        import re as _re_mm
        state._re_mm = _re_mm
        try:
            from agents.media.visual_roles import compose_scene_01_prompt as _mei_s1, pick_mei_meditation_environment as _mei_pick_env
            state._mei_s1 = _mei_s1
            state._mei_pick_env = _mei_pick_env
            state._hook_env = state._mei_pick_env(episode_seed=state.resolved_subject or '', spoken_beat=state.caption if state.caption and state.caption != '(skipped)' else '')
            state._mm_image_prompt_override = state._mei_s1(hook_env=state._hook_env, spoken_beat=state.caption if state.caption and state.caption != '(skipped)' else '', subject=state.resolved_subject or '', episode_seed=state.resolved_subject or '')
        except Exception:
            state._hook_env = 'towering jagged mountain cliff above a sea of clouds'
            state._mm_image_prompt_override = 'Master Mei — wise East Asian martial arts grandmaster with pure snow-white hair in a topknot, two long white locks framing his chest, extra-long bushy white eyebrows extending past temples, and a long white beard reaching his mid-chest. White robe and black vest with gold embroidery. Standing in deep contemplation on a mist-covered peak cliff, side-profile cinematic wide shot, sea of clouds at sunrise, mist rising.'
        state._mm_dna_clean = state.page_ctx.master_mei_visual_dna or _MEI_DNA_FALLBACK
        state._mm_dna_clean = state._re_mm.sub('\\b(?:cyber|bionic|neon|vr|headset|neural|wire|cable|implant|biomechanical)\\w*\\b', '', state._mm_dna_clean, flags=state._re_mm.IGNORECASE)
        state._mm_dna_clean = state._re_mm.sub('\\s{2,}', ' ', state._mm_dna_clean).strip(' ,.')
        state.effective_atmosphere = state._mm_image_prompt_override
        _LOG.info('master_mei | purged graphite path | Scene1 FLUX lock: %s', state._mm_image_prompt_override[:120])
    state.adapter = None
    state._carousel_image_paths = []
    state._carousel_imgbb = []
    state._images_generated_this_variant = 0
    state._reused_cover = False
    state._topic_lock = None
    state._topic_gate_extra = 0
    state._is_text_quote = state.post_format == 'TEXT_QUOTE' and (not state.skip_image)
    if state._is_text_quote:
        _stage_render_still_01(state)
    else:
        _stage_render_still_03(state)
    state.raw_bg_path = state.img_path_display if isinstance(state.img_path_display, Path) else None
    state.img_ref_engine = ''
    if state.raw_bg_path is not None:
        state.img_ref_engine = path_under_engine(app_config.ENGINE_ROOT, state.raw_bg_path)
    if not state.skip_image and (not state._reused_cover):
        state._n_hits = _track_adapter_image(state.cost_tracker, state.adapter) if state.adapter is not None else 1
        state._images_generated_this_variant = state._n_hits
        if state.cost_tracker is None:
            state._images_generated_this_variant = 1
        state._images_generated_this_variant += max(0, int(state._topic_gate_extra or 0))
    elif state._reused_cover:
        _LOG.info('IMAGE CACHE | cover billed as $0 (reused still, no API this run)')
    if state.post_type == 'CAROUSEL' and (not state.skip_image) and (state.raw_bg_path is not None) and (state.adapter is not None):
        state._carousel_quantity = max(1, int(state.carousel_quantity or 3))
        state._carousel_image_paths.append(str(state.raw_bg_path))
        state._carousel_slide_facets = ['Human scale: hands preparing the remedy, a figure at a wooden table, or someone gathering the plant — a different moment than slide 01.', 'The living landscape that produces this subject: garden, shoreline, forest floor, or kitchen garden at golden hour. Wide environmental frame.', 'Macro of the real material — salt crystals, dried leaves, oil sheen, wood grain, steam — fill the frame with texture, not a diagram.', 'Overhead still-life layout of the ingredients and tools on linen or cedar, arranged as a photograph not an infographic.', 'The ritual in motion: pouring, stirring, harvesting, or steeping. Action and change in the frame.', 'Mood and atmosphere only: silhouette, steam, window light, and negative space. No frontal product-shot repeat of slide 01.', 'The daily-life connection: the finished cup, jar, or meal in a real kitchen or table setting, warm and lived-in.']
        state._pid = (state.page_ctx.page_id if state.page_ctx else '').lower()
        for _ci in range(1, state._carousel_quantity):
            state._ci = _ci
            state._slide_num = state._ci + 1
            state._facet = state._carousel_slide_facets[(state._ci - 1) % len(state._carousel_slide_facets)]
            state._slide_prompt = build_carousel_slide_prompt(topic=state.resolved_subject, visual_subject=state.visual_subject or '', facet=state._facet, slide_num=state._slide_num, total=state._carousel_quantity, page_id=state._pid, atmosphere_style=state.effective_atmosphere or state.atmosphere_style or '')
            state._slide_lock = None
            try:
                from agents.media.scene_prompt_generator import apply_topic_visual_lock as _slide_tvl
                state._slide_tvl = _slide_tvl
                state._slide_lock = state._slide_tvl(state._slide_prompt, topic=state.resolved_subject or '', caption=f'{state.visual_subject or ''} {state._facet}', style=state.effective_atmosphere or '')
                state._slide_prompt = str(state._slide_lock.get('image_generation_prompt') or state._slide_prompt)
            except Exception:
                state._slide_lock = None
            try:
                from modules.reel_visual_qa import generate_and_gate as _slide_gate
                state._slide_gate = _slide_gate
                state._slide_img, state._slide_extra = state._slide_gate(state.adapter.generate, prompt=state._slide_prompt, chunk_text=f'{state.resolved_subject} {state._facet}', output_stem=f'{state.stem}_slide_{state._slide_num:02d}', output_directory=state.subject_assets, channel=state._pid or 'ancient_knowledge', search_dirs=[state.subject_assets] if state.subject_assets else [], generate_kwargs={'reference_image_path': state.effective_ref_path if state.image_avatar_mode == 'ON' else None, 'avatar_mode': state.image_avatar_mode, 'negative_prompt': str(state._slide_lock.get('negative_prompt') or '') if state._slide_lock else None}, banned_subjects=list(state._slide_lock.get('banned_subjects') or []) if state._slide_lock else None, domain_anchors=str((state._slide_lock or {}).get('domain_anchors') or ''), prefer_stem=state.stem)
                state._carousel_image_paths.append(str(state._slide_img))
                state._images_generated_this_variant += _track_adapter_image(state.cost_tracker, state.adapter)
                state._images_generated_this_variant += max(0, int(state._slide_extra or 0))
                _LOG.info('CAROUSEL slide %d/%d generated → %s', state._slide_num, state._carousel_quantity, Path(state._slide_img).name)
                print(f'[TOPIC-LOCK] CAROUSEL slide {state._slide_num}/{state._carousel_quantity} domain={(state._slide_lock or {}).get('domain_id') or '(none)'} banned={(state._slide_lock or {}).get('banned_subjects')}', flush=True)
            except Exception as _ce:
                state._ce = _ce
                _LOG.warning('CAROUSEL slide %d generation failed: %s — skipping.', state._slide_num, state._ce)
    elif state.post_type == 'CAROUSEL' and (not state.skip_image) and (state.raw_bg_path is not None):
        state._carousel_image_paths.append(str(state.raw_bg_path))
    state._sequence_image_paths = []
    state._early_music_thread = None
    state._early_music_box = {}
    state._early_seq_script = ''
    state._ak_voice_ready = False
    state._voice_path = None
    state._narr_dur = 0.0
    state._narr_word_timings = []
    state._ak_act_durs = None
    state._sentence_starts = []
    state._timeline_audio = 0.0
    state._asset_pool = []
    state._asset_assignments = []
    state._pool_geography = ''
    state._beat_qa_rows = []
    state._reel_qa_block_compile = False
    state._image_bill = {}
    try:
        from modules.reel_artefact.budget import reset_nano_budget as _reset_nano
        state._reset_nano = _reset_nano
        from modules.reel_artefact.budget import reset_spend as _reset_spend
        state._reset_spend = _reset_spend
        from modules.reel_artefact.budget import reset_uses as _reset_uses
        state._reset_uses = _reset_uses
        from modules.reel_artefact.checkpoint import mark_tts_resumed as _mark_tts
        state._mark_tts = _mark_tts
        from modules.reel_artefact.diversity import reset_diversity as _reset_diverse
        state._reset_diverse = _reset_diverse
        from modules.reel_artefact.logbook import reset as _reset_sources
        state._reset_sources = _reset_sources
        from modules.reel_artefact.asset_pool import reset_claims as _reset_claims
        from modules.reel_artefact.reuse import reset_approved as _reset_approved
        state._reset_approved = _reset_approved
        _reset_claims()
        state._reset_nano()
        state._reset_spend()
        state._reset_uses()
        state._reset_diverse()
        state._reset_sources()
        state._reset_approved()
        state._mark_tts(False)
    except Exception:
        pass
    try:
        from modules.reel_artefact.scene_library import start_reel as _scene_lib_start
        state._scene_lib_start = _scene_lib_start
        state._scene_lib_start((state.page_ctx.page_id if state.page_ctx else '') or 'ancient_knowledge')
    except Exception:
        pass
    state._qa_report = None
    state._episode_id = f'ep_{datetime.now().strftime('%Y%m%d_%H%M')}'
    state._episode_assets_dir = None
    if state.post_type in ('ECONOMIC_REEL', 'WAN_REEL'):
        state._episode_assets_dir = Path(app_config.ASSETS_DIR) / state._episode_id
        state._episode_assets_dir.mkdir(parents=True, exist_ok=True)
        Path(app_config.PAGE_OUTPUTS_DIR, 'clips').mkdir(parents=True, exist_ok=True)
        _LOG.info('Episode assets directory → %s', state._episode_assets_dir.resolve())
    if state.page_ctx and state.page_ctx.enable_sequence_reel and (state.post_type in ('ECONOMIC_REEL', 'WAN_REEL')) and (state.adapter is not None):
        _stage_sequence_plan_01(state)
        _stage_sequence_plan_02(state)
        _stage_sequence_plan_03(state)
    state.posting_slot_display = scheduled_bulk_post_display(variant_index=state.variation_index)
