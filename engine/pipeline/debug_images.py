# -*- coding: utf-8 -*-
from __future__ import annotations

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

"""``--test-images`` debug mode."""

def run_test_images_debug_mode(
    *,
    topic: "str | None",
    page_id: str,
    avatar_mode: str,
    post_format: str,
    n: int,
    economic: bool = True,
) -> None:
    """
    ``--test-images [N]`` debug mode.

    Generates the real production voiceover script and the first ``n`` visual
    image prompts using the SAME prompt-generation code path as a live run
    (``_build_master_mei_script_act_prompts`` for master_mei, the generic
    aligned-visual-block builder for other sequence pages), prints every
    final prompt, generates ``n`` images in parallel
    (``ThreadPoolExecutor(max_workers=5)``).

    Master Mei saves stills (768×1344) to
    ``outputs/master_mei/VisualQA_Agent_Judge/attempts/``.
    Other pages save to ``outputs/<page_id>/test_previews/``.

    Bypasses ElevenLabs voiceover synthesis, MoviePy video compilation, and
    all YouTube / B2 / ImgBB uploads entirely.
    """
    from channel_loader import load_page_context
    from agents.writer.caption_engine import CaptionEngine
    from agents.media.providers.model_router import image_model as _route_image_dbg

    n = max(1, int(n))
    page_ctx = load_page_context(page_id, avatar_mode=avatar_mode, post_format=post_format)
    resolved_subject = (topic or "").strip() or page_ctx.display_name or page_id
    is_mm = (page_ctx.page_id or "").lower() == "master_mei"
    base_style = (
        page_ctx.illustration_style.rstrip(" .")
        if page_ctx.illustration_style
        else page_ctx.atmosphere_style.rstrip(" .")
    )

    if page_ctx.uses_plan_scenes_pacing:
        seq_n = len(
            _plan_scenes(
                page_ctx.reel_duration,
                page_ctx.scene_duration,
                progressive_start_s=page_ctx.scene_progressive_start_s,
                progressive_step_every=page_ctx.scene_progressive_step_every,
                progressive_step_s=page_ctx.scene_progressive_step_s,
                progressive_cap_s=page_ctx.scene_progressive_cap_s,
                min_scenes=page_ctx.reel_image_min_count,
                max_scenes=page_ctx.reel_image_count,
            )
        )
    elif page_ctx.reel_use_hook_body_pacing:
        seq_n = _compute_hook_body_act_count(
            page_ctx.reel_duration,
            hook_hold_s=page_ctx.reel_hook_hold_s,
            body_hold_s=page_ctx.reel_body_hold_s,
            min_acts=page_ctx.reel_image_min_count,
            max_acts=page_ctx.reel_image_count,
        )
    else:
        seq_n = _compute_dense_act_count(
            page_ctx.reel_duration,
            seconds_per_act=page_ctx.reel_seconds_per_act,
            min_acts=page_ctx.reel_image_min_count,
            max_acts=page_ctx.reel_image_count,
        )
    n = min(n, seq_n)

    print("\n" + "=" * 60)
    print(f"[DEBUG] --test-images MODE | page={page_id} | subject={resolved_subject!r}")
    print(f"[DEBUG] Requested previews={n} | full production act_count={seq_n}")
    print("=" * 60)

    print("[DEBUG] Generating voiceover script…")
    ce = CaptionEngine(channel=ChannelFactory.from_env())
    script = ce.generate_sequence_voiceover(
        resolved_subject,
        page_niche=page_ctx.content_niche,
        persona_voice=page_ctx.tts_voice_preference or (
            "calm, deep, highly wise, authoritative ancient master"
            if is_mm else "investigative, neutral, immersive"
        ),
        n_acts=seq_n,
        duration_s=page_ctx.reel_duration,
        total_words_target=page_ctx.reel_narration_words,
        economic=economic,
        niche_disclaimer=page_ctx.niche_disclaimer,
        cta_line="",
        narrative_mode=page_ctx.narrative_mode,
    ) or resolved_subject
    print(f"[DEBUG] Script ({len(script.split())} words):\n{script}\n")

    # Build visual prompts for test previews.
    # Master Mei: force core Giger pillars (never Mei likeness).
    #   tech_slavery / warrior_forge / panopticon
    from agents.media.avatar_engine.mei_visual import (
        MASTER_STYLE_ANCHOR_DEFAULT as _MM_STYLE_DEFAULT,
        TEST_PREVIEW_MODULES as _MM_TEST_MODULES,
        TEST_PREVIEW_MODULES_3 as _MM_TEST_MODULES_3,
        build_test_preview_prompts as _mm_build_test_previews,
    )
    from agents.media.style_reader import default_master_mei_ref_folder as _mm_ref_folder

    _mm_pillar_keys: tuple = ()
    if is_mm:
        _mm_fixed_anchor = (
            page_ctx.master_style_anchor or _MM_STYLE_DEFAULT
        ).strip() or _MM_STYLE_DEFAULT
        _mm_style_dir = _mm_ref_folder()
        if n >= 4:
            _mm_pillar_keys = _MM_TEST_MODULES
        else:
            _mm_pillar_keys = _MM_TEST_MODULES_3[:n]
        print(
            f"[DEBUG] VISION_AGENT | style_reference folder → {_mm_style_dir}"
        )
        preview_source = _mm_build_test_previews(
            script=script,
            subject=resolved_subject,
            style_anchor=_mm_fixed_anchor,
            segment_fn=_split_script_into_act_chunks,
            module_keys=_mm_pillar_keys,
            reference_folder=_mm_style_dir,
            use_vision_style=True,
        )
        print(
            "[DEBUG] Master Mei EXCLUDED from test previews | modules: "
            + ", ".join(_mm_pillar_keys)
            + " | resolution 768x1344"
        )
    else:
        mei_slots = set()
        from agents.media.prompt_alignment import build_aligned_visual_block

        spoken_snippets = _segment_script_into_act_snippets(script, seq_n)
        act_descriptors = _build_reel_act_descriptors(
            resolved_subject, seq_n, page_id=page_ctx.page_id or ""
        )
        topic_entity_ctx = _extract_topic_visual_entities(resolved_subject)
        topic_entity_prefix = (
            f"VISUAL SUBJECT: {topic_entity_ctx}. " if topic_entity_ctx else ""
        )
        parallax_directive = (
            "DEPTH LAYERS: Foreground — dust particles, stone arch, or human observer. "
            "Background — distant temple façade, starfield, or horizon. Force multi-plane depth."
        )
        lighting_tail = (
            "LIGHTING: Dramatic directional light, volumetric shafts, high-contrast cinematic "
            "look, 35mm film grain, full-bleed, no borders."
        )
        all_prompts = []
        for i in range(seq_n):
            snippet = spoken_snippets[i] if i < len(spoken_snippets) else resolved_subject
            prev_snip = (
                spoken_snippets[i - 1] if i > 0 and (i - 1) < len(spoken_snippets) else ""
            )
            act_desc = (
                act_descriptors[i] if i < len(act_descriptors)
                else f"ACT {i + 1}: {resolved_subject}."
            )
            align = build_aligned_visual_block(
                spoken_snippet=snippet, act_index=i, total_acts=seq_n,
                main_subject=resolved_subject, prev_snippet=prev_snip,
            )
            all_prompts.append(
                f"{topic_entity_prefix}{base_style}. {act_desc} {align} "
                f"{parallax_directive}{lighting_tail}"
            )
        preview_source = all_prompts

    from agents.media.providers.together_image import sanitize_prompt_for_flux

    # Sanitize BEFORE printing so the debug output matches EXACTLY what is
    # sent to Together/FLUX (TogetherImageAdapter.generate() re-applies the
    # same idempotent sanitizer right before the API call).
    # For master_mei, preview_source is already exactly the pillar prompt set.
    _take = len(preview_source) if is_mm else n
    test_prompts = [sanitize_prompt_for_flux(p) for p in preview_source[:_take]]
    for idx, p in enumerate(test_prompts, start=1):
        label = ""
        if is_mm and _mm_pillar_keys and idx <= len(_mm_pillar_keys):
            label = f" [{_mm_pillar_keys[idx - 1]}]"
        print(f"[DEBUG] Final Prompt Sent to API [{idx}/{len(test_prompts)}]{label}: {p}")

    # ------------------------------------------------------------------
    # Image generation — N images in parallel (max_workers=5)
    # ------------------------------------------------------------------
    if is_mm:
        out_dir = (
            page_outputs_dir(page_id) / "VisualQA_Agent_Judge" / "attempts"
        )
    else:
        out_dir = page_outputs_dir(page_id) / "test_previews"
    out_dir.mkdir(parents=True, exist_ok=True)

    _page_cost = page_ctx.cost_tier
    _img_route = _route_image_dbg(
        task="image",
        page_cost_tier=_page_cost,
        model_override=page_ctx.image_model_override,
        preferred=(
            app_config.GEMINI_ECONOMIC_IMAGE_MODEL if economic else app_config.GEMINI_IMAGE_MODEL
        ),
        use_premium=True if (_page_cost or "").lower() == "premium" else None,
        log=True,
    )
    img_model_id = app_config.normalize_image_model_id(_img_route.model_id)
    # Routes to RemoteGPUImageAdapter when ENABLE_REMOTE_GPU_WORKFLOWS=true
    adapter = get_image_adapter(
        model_id=img_model_id, page_cost_tier=_page_cost, tier=_img_route.tier,
    )
    from agents.media.free_images.integrate import wrap_image_generate

    adapter.generate = wrap_image_generate(
        adapter.generate,
        channel=page_ctx.page_id,
        page_cfg=page_ctx.page_cfg,
        query="",
        producer="reel_image",
    )

    # --test-images for master_mei: never attach avatar.png (Mei excluded).
    style_refs = page_ctx.resolve_style_reference_images()
    style_weight = page_ctx.style_reference_weight
    print(
        f"[DEBUG] Loaded {len(style_refs)} style reference assets for {page_id}: "
        f"{[p.name for p in style_refs]} | weight={style_weight}"
    )
    if is_mm:
        print(f"[DEBUG] Draft output dir -> {out_dir}")

    jobs: "dict[int, Any]" = {}
    for idx, p in enumerate(test_prompts):
        _p_lo = (p or "").lower()
        _skip_sref = is_mm and any(
            tok in _p_lo
            for tok in (
                "fitness model physique",
                "glossy led fashion",
                "clean high-fashion",
            )
        )
        jobs[idx] = functools.partial(
            adapter.generate,
            p,
            output_stem=f"attempt_{idx + 1:02d}" if is_mm else f"test_preview_act{idx + 1:02d}",
            output_directory=out_dir,
            reference_image_path=None,
            avatar_mode="OFF",
            reference_image_weight=None,
            style_reference_paths=None if _skip_sref else (style_refs or None),
            style_reference_weight=None if _skip_sref else style_weight,
            draft=bool(is_mm),
        )

    print(
        f"\n[DEBUG] Generating {len(jobs)} test image(s) in parallel "
        f"(ThreadPoolExecutor max_workers=5) → {out_dir}"
    )
    results = _run_acts_parallel(jobs, max_workers=5)

    ok = 0
    for idx in range(len(test_prompts)):
        res = results.get(idx)
        if isinstance(res, Exception):
            print(f"[DEBUG] Image {idx + 1}/{n} FAILED: {res}")
        else:
            print(f"[DEBUG] Image {idx + 1}/{n} OK -> {res}")
            ok += 1

    print(
        f"\n[DEBUG] --test-images complete | {ok}/{len(test_prompts)} succeeded | "
        f"saved to {out_dir}"
    )
    print(
        "[DEBUG] Bypassed: ElevenLabs voiceover synthesis, MoviePy video compilation, "
        "YouTube/B2/ImgBB uploads."
    )
