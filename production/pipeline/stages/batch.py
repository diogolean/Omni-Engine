# -*- coding: utf-8 -*-
from __future__ import annotations
from shared.channel_flags import default_page

from production.pipeline.channel_flags import enabled as _channel_enabled

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

"""Batch produce(). The variant worker and agentic path stay behind the same calls."""

from production.pipeline.stages.agentic import _persist_agentic_postplanner, _produce_agentic
from production.pipeline.stages.variant import _produce_variant_worker

def produce(
    subject: str | None,
    *,
    quantity: int = 1,
    skip_image: bool = False,
    skip_caption: bool = False,
    test_mode: bool = False,
    economic_brain_mode: bool | None = None,
    bootstrap_models: PlannedModels | None = None,
    page_ctx: PageContext | None = None,
    cta_enabled: bool = True,
    post_type: str = "STANDARD_QUOTE",
    carousel_quantity: int = 3,
    image_style: str = "NATURAL",
    render_approval_required: bool = False,
    agentic_pipeline: bool | None = None,
    multi_variant: bool = False,
    text_overlay: bool | None = None,
) -> dict[str, Any]:
    qty = max(1, quantity)
    economic = economic_brain_mode if economic_brain_mode is not None else app_config.ECONOMIC_BRAIN_MODE

    # Page-level override: if page_config.py sets ECONOMIC_BRAIN_MODE explicitly,
    # honour it unless the caller explicitly passed economic_brain_mode as non-None.
    if economic_brain_mode is None and page_ctx is not None:
        _page_econ = page_ctx.page_economic_brain_mode
        if _page_econ is not None:
            economic = _page_econ
            _LOG.info(
                "Page-level economic override | page=%s ECONOMIC_BRAIN_MODE=%s",
                page_ctx.page_id, economic,
            )

    # Resolve avatar_mode and post_format from page context (or safe defaults).
    avatar_mode: str = page_ctx.avatar_mode if page_ctx else "ON"
    post_format: str = page_ctx.post_format if page_ctx else "IMAGE_AVATAR"
    # ECONOMIC_REEL / WAN_REEL must not fall back into the IMAGE_AVATAR image-post pipeline.
    if post_type in ("ECONOMIC_REEL", "WAN_REEL"):
        post_format = "DYNAMIC_REEL"
    atmosphere_style: str = page_ctx.atmosphere_style if page_ctx else ""

    # Page-level aspect ratio override (falls back to global config if empty).
    page_aspect_ratio: str = (page_ctx.image_aspect_ratio if page_ctx else "") or ""

    bm = bootstrap_models or _snapshot_verified_models(
        economic_brain_mode=economic,
        page_ctx=page_ctx,
    )
    _bootstrap_pipeline_intro(
        economic_brain_mode=economic,
        verified=bm,
        compact=not test_mode,
        page_ctx=page_ctx,
    )

    envelope: dict[str, Any] = {
        "mode": "test" if test_mode else "live",
        "quantity": qty,
        "economic_brain_mode": economic,
        "page_id": page_ctx.page_id if page_ctx else app_config.ACTIVE_PAGE,
        "avatar_mode": avatar_mode,
        "post_format": post_format,
        "post_type": post_type,
        "carousel_quantity": int(carousel_quantity or 3),
        "multi_variant": bool(multi_variant),
        "text_overlay": text_overlay,
        "cta_enabled": cta_enabled,
        "items": [],
    }

    pdf_inventory = list_pdf_relative_paths(app_config.DIGITAL_PRODUCTS_PATH)

    if test_mode:
        topic_seed = (subject or "").strip() or "Auto subject imaginer (provide subject for production)"
        _LOG.info("TEST MODE scaffold | topic_hint=%s | quantity=%s", topic_seed, qty)
        print("\n=== TEST MODE - no Gemini or Anthropic network calls ===\n")
        print(f"=== Page: {page_ctx.display_name if page_ctx else default_page()} "
              f"| Avatar: {avatar_mode} | Format: {post_format} ===\n")

        print("--- Knowledge test: PDF corpus inventory ---\n")
        if pdf_inventory:
            for name in pdf_inventory:
                print(f"  - {name}")
        else:
            print(
                f"  (No PDF files under `{app_config.DIGITAL_PRODUCTS_PATH.resolve()}`. "
                "Brain cannot ingest guides until PDFs arrive.)",
            )

        imagine_prompt = imagine_subject_instruction_preview()
        architect = VisualArchitect(channel=ChannelFactory.from_env())
        prompt = architect.build_prompt(
            topic_seed,
            variation_index=0,
            total_variants=qty,
            avatar_mode=avatar_mode,
            atmosphere_style=atmosphere_style,
            aspect_ratio=page_aspect_ratio or None,
        )
        researcher_instruction = build_gemini_researcher_instruction(topic_seed)
        sys_prompt, usr_prompt = humanizer_preview_with_placeholder(
            topic_seed, cta_enabled=cta_enabled,
        )

        envelope["digital_products_pdf_files"] = pdf_inventory
        envelope["imagine_subject_instruction"] = imagine_prompt
        envelope["visual_prompt"] = prompt

        print("\n--- Imagine-subject scaffold (Brain) ---\n")
        print(imagine_prompt)
        print("\n--- Visual test (upstream image prompt) ---\n")
        print(prompt)
        print("\n--- Gemini researcher scaffold ---\n")
        print(researcher_instruction)
        print("\n--- Claude humanizer (system) ---\n")
        print(sys_prompt)
        print("\n--- Claude humanizer (user scaffold; FACT SHEET dynamic in live runs) ---\n")
        print(usr_prompt)
        print("\n--- Economic Gemini-only humanizer scaffold ---\n")
        print(economic_humanizer_instruction_preview(topic_seed, cta_enabled=cta_enabled))

        if post_type == "LONG_CAPTION_IMAGE":
            _cta_kw = contextual_cta_keyword(topic_seed) if cta_enabled else None
            _sig = (page_ctx.caption_signature if page_ctx else "") or ""
            _long_prompt = long_caption_prompt_preview(
                topic_seed,
                page_display_name=page_ctx.display_name if page_ctx else "",
                page_niche=page_ctx.content_niche if page_ctx else "",
                cta_enabled=cta_enabled,
                cta_keyword=_cta_kw,
                signature=_sig,
            )
            print(
                f"\n--- LONG_CAPTION_IMAGE prompt scaffold | "
                f"cta={'ON' if cta_enabled else 'OFF'} | "
                f"keyword={_cta_kw or '(suppressed)'} ---\n"
            )
            print(_long_prompt)

        if skip_image or skip_caption:
            print(
                "\n[hint] `--skip-image` / `--skip-caption` are informational in `--test`; "
                "scaffolds still print.\n",
            )

        envelope["items"].append(
            {
                "topic": topic_seed,
                "caption": "(dry-run)",
                "local_image_path": "(dry-run)",
                "imgbb_url": "",
                "variant_index": 0,
            }
        )
        return envelope

    corpus = load_digital_product_corpus(
        app_config.DIGITAL_PRODUCTS_PATH,
        chunk_char_limit=app_config.PDF_CHUNK_CHAR_LIMIT,
    )

    _silence_noisy_http_loggers()
    import random as _rnd
    _page_id_lower = (page_ctx.page_id if page_ctx else "").lower()
    _channel = ChannelFactory.from_env()
    _isolated = ChannelFactory.is_isolated(_page_id_lower or _channel.channel_id)

    resolved_subject = (subject or "").strip()
    _cli_topic = resolved_subject
    if _isolated:
        if resolved_subject and _channel.topic_is_off_niche(resolved_subject):
            _LOG.warning(
                "AK TOPIC HARD-OVERRIDE | discarding leaked/stale topic %r",
                resolved_subject,
            )
            resolved_subject = ""
            _cli_topic = ""
        if not resolved_subject:
            _recent_ak = _recent_library_topics(app_config.CONTENT_LIBRARY_PATH, 40)
            _fresh = select_distinct_pool_topics(
                _channel.get_niche_topics(), 1,
                recent_topics=_recent_ak, rng=_rnd,
            )
            resolved_subject = _fresh[0] if _fresh else _channel.pick_niche_topic()
            _LOG.info(
                "AK TOPIC HARD-OVERRIDE | selected niche topic → %r (bank=%d, library_filtered=%d)",
                resolved_subject,
                len(_channel.get_niche_topics()),
                len(_recent_ak),
            )
    elif not resolved_subject:
        # CAROUSEL (and any page with a topic queue): prefer a fresh pool
        # subject over a Gemini imagine_subject call so --quantity batches
        # stay on distinct, channel-owned topics even when credits are tight.
        _pool_for_one = list(page_ctx.topic_pool if page_ctx else []) or list(
            getattr(_channel, "get_niche_topics", lambda: [])() or []
        )
        if post_type == "CAROUSEL" and _pool_for_one:
            _recent_one = _recent_library_topics(app_config.CONTENT_LIBRARY_PATH, 40)
            _fresh_one = select_distinct_pool_topics(
                _pool_for_one, 1, recent_topics=_recent_one, rng=_rnd,
            )
            resolved_subject = _fresh_one[0] if _fresh_one else _pool_for_one[0]
            _LOG.info(
                "CAROUSEL TOPIC | pool subject → %r (bank=%d)",
                resolved_subject, len(_pool_for_one),
            )
        else:
            resolved_subject = imagine_subject(corpus)

    # WONDER_FEED + MASTER_MEI reels: ALWAYS pick a fresh topic from the TOPIC_POOL.
    # The documentary page is handled above and never reads the PDF/wellness corpus.
    if (
        (not _isolated)
        and _channel_enabled(page_ctx, 'POOL_TOPIC_LOCK')
        and post_type in ("ECONOMIC_REEL", "SMART_BAIT")
        and page_ctx is not None
        and page_ctx.topic_pool
    ):
        resolved_subject = _rnd.choice(page_ctx.topic_pool)
        if _channel_enabled(page_ctx, 'MEI_PIPELINE'):
            from production.writers.mei_narrative import episode_theme_meta
            _ep = episode_theme_meta(resolved_subject)
            _LOG.info(
                "%s TOPIC LOCK | episode=%s | theme=%s | topic=%r (pool size=%d)",
                _page_id_lower, _ep["label"], _ep["key"],
                resolved_subject, len(page_ctx.topic_pool),
            )
        else:
            _LOG.info(
                "%s TOPIC LOCK | fresh pool topic selected → %r (pool size=%d)",
                _page_id_lower, resolved_subject, len(page_ctx.topic_pool),
            )
    else:
        # Generic fallback: replace only the known static placeholder with a pool topic.
        _STATIC_FALLBACK_SUBJECT = "Holistic vitality protocol"
        if (
            resolved_subject == _STATIC_FALLBACK_SUBJECT
            and page_ctx is not None
            and page_ctx.topic_pool
        ):
            resolved_subject = _rnd.choice(page_ctx.topic_pool)
            _LOG.info(
                "Topic pool override | static fallback replaced → %r (pool size=%d)",
                resolved_subject, len(page_ctx.topic_pool),
            )

    _LOG.info(
        "PIPELINE LIVE | page=%s | avatar=%s | format=%s | resolved_subject=%r | qty=%s | economic=%s",
        page_ctx.page_id if page_ctx else app_config.ACTIVE_PAGE,
        avatar_mode,
        post_format,
        resolved_subject,
        qty,
        economic,
    )

    # ── BATCH PLANNER ────────────────────────────────────────────────────────
    # Default --quantity N: N completely distinct subjects (pool / library /
    # content queue). Sub-angle deconstruction runs ONLY when --multi-variant
    # is explicitly set. CAROUSEL never inherits "N variants of one topic".
    batch_angles: list[BatchAngle] | None = None
    uniqueness_guard: BatchUniquenessGuard | None = None
    global_topic_dna = (_cli_topic or resolved_subject or "").strip()
    per_variant_topics: list[str] | None = None
    _seed_for_angles: list[str] | None = None
    _use_sub_angles = bool(multi_variant) and qty > 1

    if qty > 1:
        uniqueness_guard = BatchUniquenessGuard()

    if qty > 1 and not _use_sub_angles:
        _lookback = 40
        if page_ctx is not None:
            try:
                _lookback = int(page_ctx.page_cfg.get("CONTENT_LIBRARY_LOOKBACK", 40))
            except (TypeError, ValueError):
                _lookback = 40
        _recent = _recent_library_topics(app_config.CONTENT_LIBRARY_PATH, _lookback)
        _pool = list(page_ctx.topic_pool if page_ctx else []) or list(
            getattr(_channel, "get_niche_topics", lambda: [])() or []
        )
        per_variant_topics = plan_distinct_batch_topics(
            qty,
            pool=_pool,
            recent_topics=_recent,
            cli_topic=_cli_topic,
            seed_topic="" if _cli_topic else resolved_subject,
            rng=_rnd,
        )
        if len(per_variant_topics or []) < qty:
            _need = qty - len(per_variant_topics or [])
            _llm_topics = generate_bulk_topics(
                _need,
                page_id=_page_id_lower,
                page_niche=page_ctx.content_niche if page_ctx else "",
            )
            if per_variant_topics is None:
                per_variant_topics = []
            _have = {_topic_theme_key(t) for t in per_variant_topics}
            for _t in _llm_topics:
                _k = _topic_theme_key(_t)
                if not _t or _k in _have:
                    continue
                per_variant_topics.append(_t)
                _have.add(_k)
                if len(per_variant_topics) >= qty:
                    break
        if per_variant_topics:
            per_variant_topics = per_variant_topics[:qty]
            resolved_subject = per_variant_topics[0]
            global_topic_dna = resolved_subject
        batch_angles = None
        envelope["global_topic_dna"] = global_topic_dna
        envelope["batch_topics"] = list(per_variant_topics or [])
        _LOG.info(
            "BatchPlanner | DISTINCT TOPICS | page=%s | qty=%d | lookback=%d | %s",
            _page_id_lower,
            len(per_variant_topics or []),
            _lookback,
            " | ".join(per_variant_topics or []),
        )
    elif qty > 1 and _use_sub_angles:
        # Explicit --multi-variant: deconstruct one core topic into N angles.
        if not _cli_topic:
            _pool = page_ctx.topic_pool if page_ctx else []
            if _pool and len(_pool) >= qty:
                _pool_copy = list(_pool)
                _rnd.shuffle(_pool_copy)
                per_variant_topics = [resolved_subject] + [
                    t for t in _pool_copy if t != resolved_subject
                ][:qty - 1]
                _seed_for_angles = list(per_variant_topics)
        _core = (_cli_topic or "").strip() or global_topic_dna or (
            page_ctx.content_niche if page_ctx else ""
        ) or "Content series"
        global_topic_dna = _core
        batch_angles = plan_angles_matrix(
            _core,
            qty,
            page_id=_page_id_lower,
            page_niche=page_ctx.content_niche if page_ctx else "",
            seed_topics=_seed_for_angles or per_variant_topics,
        )
        per_variant_topics = [a.combined_topic for a in batch_angles]
        if batch_angles:
            resolved_subject = batch_angles[0].combined_topic
        envelope["batch_angles"] = [
            {
                "index": a.index,
                "angle_title": a.angle_title,
                "hook_style": a.hook_style,
                "visual_focus": a.visual_focus,
                "seo_title_hint": a.seo_title_hint,
            }
            for a in batch_angles
        ]
        envelope["global_topic_dna"] = global_topic_dna
        _LOG.info(
            "BatchPlanner | MULTI-VARIANT angles | angles=%d | core=%r",
            len(batch_angles), global_topic_dna,
        )

    logging.info(
        "Models banner | verified_image=%s | verified_research=%s | humanizer=%s",
        bm.image_primary_id,
        bm.research_primary_id,
        bm.humanizer_summary,
    )

    slug = subject_slug(resolved_subject)
    subject_assets = app_config.ASSETS_DIR / slug
    subject_assets.mkdir(parents=True, exist_ok=True)

    caption_engine: CaptionEngine | None = None
    if not skip_caption:
        try:
            caption_engine = CaptionEngine(channel=ChannelFactory.from_env())
            _LOG.info("CaptionEngine online | Gemini text head=`%s`", caption_engine.research_primary_id)
        except Exception as exc:  # noqa: BLE001
            _LOG.error("CaptionEngine init failed: %s", exc, exc_info=True)
            logging.error(
                "FATAL_BEFORE_EXIT | CaptionEngine init | stage=initialization | Gemini_text_head=%s | err=%s",
                bm.research_primary_id,
                exc,
            )
            raise

    if _isolated:
        if per_variant_topics:
            _fixed_topics: list[str] = []
            for _t in per_variant_topics:
                if _channel.topic_is_off_niche(_t):
                    _repl = select_distinct_pool_topics(
                        _channel.get_niche_topics(), 1,
                        reserved=_fixed_topics, rng=_rnd,
                    )
                    _fixed_topics.append(
                        _repl[0] if _repl else _channel.pick_niche_topic()
                    )
                else:
                    _fixed_topics.append(_t)
            per_variant_topics = _fixed_topics
            resolved_subject = per_variant_topics[0]
        elif _channel.topic_is_off_niche(resolved_subject):
            resolved_subject = _channel.pick_niche_topic()
        global_topic_dna = resolved_subject
        _LOG.info(
            "AK TOPIC LOCK FINAL | resolved_subject=%r | variants=%s",
            resolved_subject,
            len(per_variant_topics or [resolved_subject]),
        )

    envelope["resolved_subject"] = resolved_subject

    from production.pipeline.agentic import should_use_agentic_pipeline as _should_agentic
    from production.pipeline.agentic.criteria import MAX_RETRIES as _AK_PRINT_RETRIES

    if _should_agentic(
        page_ctx.page_id if page_ctx else None,
        post_type,
        agentic_pipeline,
    ):
        _LOG.info(
            "AGENTIC PIPELINE | page=%s | qty=%s | ThemeCurator→Copywriter→VisualDirector⇄VisualQA",
            page_ctx.page_id if page_ctx else app_config.ACTIVE_PAGE,
            qty,
        )
        print(
            "\n[agentic] 4-tier pipeline | "
            "ThemeCurator → Copywriter → VisualDirector ⇄ VisualQA "
            f"(max_retries={_AK_PRINT_RETRIES} → r01+r02) | qty={qty}"
        )
        return _produce_agentic(
            resolved_subject=resolved_subject,
            qty=qty,
            skip_image=skip_image,
            skip_caption=skip_caption,
            page_ctx=page_ctx,
            post_type=post_type,
            post_format=post_format,
            caption_engine=caption_engine,
            per_variant_topics=per_variant_topics,
            envelope_base=envelope,
            carousel_quantity=int(carousel_quantity or 3),
        )

    run_stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    postplanner_dir = app_config.PAGE_OUTPUTS_DIR / "postplanner"
    logs_dir = pipeline_logs_dir()

    # ------------------------------------------------------------------
    # ONE-CALL BATCH RESEARCH: generate all variant narratives upfront.
    # Skipped for SMART_BAIT (no research phase needed) and economic mode.
    # ------------------------------------------------------------------
    pre_narratives: list[str] = []
    if not skip_caption and not economic and post_type not in ("SMART_BAIT", "ECONOMIC_REEL") and caption_engine is not None:
        try:
            pre_narratives = caption_engine.synthesize_facts_batch(
                resolved_subject, corpus, num_variants=qty
            )
            _LOG.info(
                "ONE-CALL batch research: %d/%d narratives ready for '%s'.",
                sum(1 for n in pre_narratives if n),
                qty,
                resolved_subject,
            )
        except Exception as batch_exc:  # noqa: BLE001
            _LOG.warning(
                "Batch research failed (%s). Falling back to per-variant calls.", batch_exc
            )

    # Pre-compute the effective reference image path once — it is constant
    # across all variants and depends only on avatar_mode and page_ctx.
    effective_ref_path: Path | None = None
    if avatar_mode == "ON":
        # Mei pipeline: hard-prefer forced avatar.png DNA over cycled avatars
        if (
            page_ctx
            and _channel_enabled(page_ctx, 'MEI_PIPELINE')
            and not page_ctx.sequence_force_avatar_off
        ):
            _forced_ref = page_ctx.forced_avatar_reference_path
            if _forced_ref is not None and _forced_ref.is_file():
                effective_ref_path = _forced_ref
        if effective_ref_path is None and page_ctx and page_ctx.avatar_reference_exists:
            # Prefer cycling pool (assets/{page}/avatars) when available
            _cycled = page_ctx.cycle_avatar_reference(0)
            effective_ref_path = _cycled or page_ctx.avatar_reference_png
        elif effective_ref_path is None and page_ctx:
            _cycled = page_ctx.cycle_avatar_reference(0)
            if _cycled is not None:
                effective_ref_path = _cycled
            else:
                effective_ref_path = app_config.REFERENCE_IMAGE_PATH
        elif effective_ref_path is None:
            effective_ref_path = app_config.REFERENCE_IMAGE_PATH

    econ_model = app_config.GEMINI_ECONOMIC_BRAIN_MODEL

    # ------------------------------------------------------------------
    # Parallel variant execution
    # ------------------------------------------------------------------
    # write_lock serialises all writes to shared files (Excel workbooks,
    # content_library.json) so concurrent workers never interleave rows.
    write_lock = threading.Lock()

    # ── DYNAMIC SHORT-TERM HOOK MEMORY ───────────────────────────────────────
    # Load any hooks generated in prior runs for this page so the LLM always
    # has a growing list of already-used angles to avoid.  A simple JSON file
    # acts as the lightweight persistent store across sequential CLI calls.
    # Thread-safe: workers read a snapshot at call time; appends are locked.
    _hooks_cache_path = getattr(
        app_config,
        "SESSION_HOOKS_CACHE_PATH",
        app_config.CHANNEL_STORE_DIR / "session_hooks_cache.json",
    )
    generated_hooks_cache: list[str] = []
    try:
        import json as _jc
        from shared.store import hydrate_state_file

        _hooks_cache_path = hydrate_state_file(_hooks_cache_path)
        if _hooks_cache_path.exists():
            _raw = _hooks_cache_path.read_text(encoding="utf-8")
            _loaded = _jc.loads(_raw)
            if isinstance(_loaded, list):
                generated_hooks_cache = [str(h) for h in _loaded if h]
                _LOG.info(
                    "Hooks cache loaded: %d prior hooks from %s",
                    len(generated_hooks_cache),
                    _hooks_cache_path.name,
                )
    except Exception as _hce:  # noqa: BLE001
        _LOG.warning("Could not load hooks cache (%s) — starting fresh.", _hce)
        generated_hooks_cache = []
    hooks_cache_lock = threading.Lock()
    batch_image_counter: list[int] = [0]
    batch_image_lock = threading.Lock()

    _wkw: dict[str, Any] = dict(
        qty=qty,
        slug=slug,
        resolved_subject=resolved_subject,
        per_variant_topics=per_variant_topics,
        batch_angles=batch_angles,
        uniqueness_guard=uniqueness_guard,
        global_topic_dna=global_topic_dna,
        corpus=corpus,
        pre_narratives=pre_narratives,
        caption_engine=caption_engine,
        skip_image=skip_image,
        skip_caption=skip_caption,
        avatar_mode=avatar_mode,
        post_format=post_format,
        atmosphere_style=atmosphere_style,
        page_aspect_ratio=page_aspect_ratio,
        effective_ref_path=effective_ref_path,
        economic=economic,
        econ_model=econ_model,
        bm=bm,
        page_ctx=page_ctx,
        subject_assets=subject_assets,
        run_stamp=run_stamp,
        postplanner_dir=postplanner_dir,
        logs_dir=logs_dir,
        write_lock=write_lock,
        cta_enabled=cta_enabled,
        post_type=post_type,
        carousel_quantity=int(carousel_quantity or 3),
        image_style=image_style,
        text_overlay_enabled=text_overlay,
        generated_hooks_cache=generated_hooks_cache,
        hooks_cache_lock=hooks_cache_lock,
        hooks_cache_path=_hooks_cache_path,
        batch_image_counter=batch_image_counter,
        batch_image_lock=batch_image_lock,
        render_approval_required=bool(render_approval_required),
    )

    # Batch CostTracker is the merge target. Each worker forks a per-reel
    # tracker so parallel variants cannot mix global accumulators.
    _cost_tracker: CostTracker | None = None
    if page_ctx is not None and page_ctx.enable_cost_tracking:
        _cost_tracker = CostTracker(
            page_id=page_ctx.page_id,
            cost_tier=page_ctx.cost_tier,
        )
        _LOG.info(
            "CostTracker enabled | page=%s tier=%s",
            page_ctx.page_id, page_ctx.cost_tier,
        )
    _wkw["cost_tracker"] = _cost_tracker

    if qty == 1:
        # Single-variant path: exceptions propagate naturally to cli()
        # so the clean API-error display and logging remain intact.
        raw_results = [_produce_variant_worker(0, **_wkw)]
    else:
        # Multi-variant path: each worker runs concurrently; a failure in
        # one variant is caught per-future and logged, all others complete.
        max_workers = min(qty, 3)
        _LOG.info(
            "Bulk production: %d variants | max_workers=%d | page=%s",
            qty,
            max_workers,
            page_ctx.page_id if page_ctx else app_config.ACTIVE_PAGE,
        )
        print(f"\n[bulk] Launching {qty} concurrent variant workers (max_workers={max_workers})…")
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futs = {
                pool.submit(_produce_variant_worker, v, **_wkw): v
                for v in range(qty)
            }
            raw_results: list[dict[str, Any] | None] = []
            for fut in as_completed(futs):
                v_idx = futs[fut]
                try:
                    raw_results.append(fut.result())
                    print(f"  [bulk] Variant {v_idx + 1}/{qty} complete.")
                except Exception as exc:  # noqa: BLE001
                    _LOG.error(
                        "Variant %d failed and was skipped: %s",
                        v_idx + 1,
                        exc,
                        exc_info=True,
                    )
                    print(
                        f"  [bulk] Variant {v_idx + 1}/{qty} FAILED (skipped) — "
                        f"{type(exc).__name__}: {exc}"
                    )
                    raw_results.append(None)

    items: list[dict[str, Any]] = [r for r in raw_results if r is not None]
    items.sort(key=lambda x: x["variant_index"])

    skipped_count = qty - len(items)
    if skipped_count:
        _LOG.warning(
            "%d of %d variant(s) failed and were excluded from the output.",
            skipped_count,
            qty,
        )

    # ── FINAL COST SNAPSHOT: merged batch tracker after all workers return ──
    _total_images_generated = sum(r.get("images_generated", 0) for r in items)
    envelope["total_images_generated"] = _total_images_generated
    if _cost_tracker is not None and page_ctx is not None and page_ctx.enable_cost_tracking:
        envelope["final_cost_breakdown"] = _cost_tracker.to_dict()
        envelope["estimated_cost_usd"] = round(_cost_tracker.pipeline_usd(), 6)
        _LOG.info(
            "Final cost snapshot | api_images=%d | pipeline=$%.6f | tier=%s",
            _total_images_generated,
            _cost_tracker.pipeline_usd(),
            _cost_tracker.cost_tier,
        )

    envelope["items"] = items

    snippet_lines: list[str] = []
    for idx, row in enumerate(items):
        cap = row.get("caption")
        if isinstance(cap, str):
            snippet_lines.append(f"{idx + 1}. {cap}")
    snippet = "\n".join(snippet_lines)
    snippet_path = app_config.PAGE_OUTPUTS_DIR / "last_captions_bundle.txt"
    if snippet.strip():
        snippet_path.write_text(snippet.strip() + "\n", encoding="utf-8")

    _LOG.info("PIPELINE DONE | page=%s | artifacts under outputs/%s/",
              page_ctx.page_id if page_ctx else app_config.ACTIVE_PAGE,
              page_ctx.page_id if page_ctx else app_config.ACTIVE_PAGE)
    return envelope
def run_pipeline(
    topic: str,
    *,
    skip_image: bool = False,
    skip_caption: bool = False,
    test_mode: bool = False,
) -> dict[str, Any]:
    """Backward-compatible alias for scripts expecting the older entrypoint."""
    bm = _snapshot_verified_models(economic_brain_mode=app_config.ECONOMIC_BRAIN_MODE)
    return produce(
        topic.strip() if topic else None,
        quantity=1,
        skip_image=skip_image,
        skip_caption=skip_caption,
        test_mode=test_mode,
        economic_brain_mode=None,
        bootstrap_models=bm,
        page_ctx=None,
    )
