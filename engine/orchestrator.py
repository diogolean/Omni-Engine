# -*- coding: utf-8 -*-
"""orchestrator steps moved out of main.py. Bodies are unchanged."""
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
def _produce_variant_worker(
    variant: int,
    *,
    qty: int,
    slug: str,
    resolved_subject: str,
    corpus: Any,
    pre_narratives: list[str],
    caption_engine: "CaptionEngine | None",
    skip_image: bool,
    skip_caption: bool,
    avatar_mode: str,
    post_format: str,
    atmosphere_style: str,
    page_aspect_ratio: str,
    effective_ref_path: "Path | None",
    economic: bool,
    econ_model: str,
    bm: "PlannedModels",
    page_ctx: "PageContext | None",
    subject_assets: Path,
    run_stamp: str,
    postplanner_dir: Path,
    logs_dir: Path,
    write_lock: threading.Lock,
    cta_enabled: bool = True,
    post_type: str = "STANDARD_QUOTE",
    carousel_quantity: int = 3,
    image_style: str = "NATURAL",
    text_overlay_enabled: bool | None = None,
    generated_hooks_cache: "list[str] | None" = None,
    hooks_cache_lock: "threading.Lock | None" = None,
    hooks_cache_path: "Path | None" = None,
    cost_tracker: "CostTracker | None" = None,
    per_variant_topics: "list[str] | None" = None,
    batch_angles: "list[BatchAngle] | None" = None,
    uniqueness_guard: "BatchUniquenessGuard | None" = None,
    global_topic_dna: str = "",
    batch_image_counter: "list[int] | None" = None,
    batch_image_lock: "threading.Lock | None" = None,
    render_approval_required: bool = False,
) -> "dict[str, Any]":
    """
    Produce one complete post variant inside a worker thread.

    Handles image generation, ImgBB upload, caption research, humanisation,
    and all durable artefact writes for variant ``variant`` (0-based index).

    File writes that touch shared resources (Excel workbooks,
    content_library.json) are serialised through ``write_lock`` so concurrent
    workers never corrupt each other's output.

    Raises on fatal errors so the ThreadPoolExecutor caller can catch them
    per-future; for qty==1 the exception propagates naturally to cli().
    """
    stem = f"{slug}_v{variant + 1:02d}"
    variation_index = variant
    _wan_t0_variant = time.monotonic()
    _wan_stage_times: dict[str, float] = {}
    _wan_act_status: list[str] = []
    batch_cost_tracker = cost_tracker
    if batch_cost_tracker is not None:
        cost_tracker = CostTracker(
            page_id=batch_cost_tracker.page_id,
            cost_tier=batch_cost_tracker.cost_tier,
        )

    # ── Batch Angle injection (qty > 1) — Global DNA + unique sub-angle ─────
    _batch_angle: "BatchAngle | None" = None
    _batch_angle_block = ""
    _visual_angle_focus = ""
    if batch_angles and variant < len(batch_angles):
        _batch_angle = batch_angles[variant]
        _batch_angle_block = _batch_angle.prompt_block()
        _visual_angle_focus = (_batch_angle.visual_focus or "").strip()
        resolved_subject = _batch_angle.combined_topic
        slug = subject_slug(resolved_subject)
        stem = f"{slug}_v{variant + 1:02d}"
        subject_assets = app_config.ASSETS_DIR / slug
        subject_assets.mkdir(parents=True, exist_ok=True)
        _LOG.info(
            "Variant %d/%d | BATCH ANGLE → %r | hook_style=%s | global=%r",
            variant + 1,
            _batch_angle.total,
            _batch_angle.angle_title,
            _batch_angle.hook_style,
            _batch_angle.global_topic,
        )
    # ── Per-variant topic override (legacy pool path when no angles matrix) ──
    elif per_variant_topics and variant < len(per_variant_topics):
        _vt = per_variant_topics[variant]
        if _vt and _vt != resolved_subject:
            resolved_subject = _vt
            slug = subject_slug(resolved_subject)
            stem = f"{slug}_v{variant + 1:02d}"
            subject_assets = app_config.ASSETS_DIR / slug
            subject_assets.mkdir(parents=True, exist_ok=True)
            _LOG.info(
                "Variant %d | per-variant topic override → %r",
                variant + 1, resolved_subject,
            )

    if page_ctx and (page_ctx.page_id or "").lower() == "ancient_knowledge":
        from modules.reel_artefact.provider_health import preflight_text as _preflight_text

        _preflight_text("ancient_knowledge")

    cta_kw = contextual_cta_keyword(resolved_subject)
    humanizer_notes = bm.humanizer_summary

    caption: str = "(skipped)"
    raw_sheet: str = "(skipped)"
    img_path_display: Path | str = "(skipped)"
    caption_mode_tag: str | None = None
    durable_abs: Path | None = None
    planner_row_ix: int | None = None
    video_path_str: str = ""

    # ====================================================================
    # PHASE B1: Caption types that run BEFORE image generation
    #   SMART_BAIT    — overlay_text drives the image prompt atmosphere
    #   LONG_CAPTION_IMAGE — caption is generated here; image uses illustration_style
    #   CTA_CAPTION_IMAGE  — original Anna caption (research + Comment KEYWORD); logo-only still
    # ====================================================================
    overlay_text: str = ""
    visual_subject: str = ""  # LLM-authored scene description for image generation
    _seo_title_local = ""

    if post_type in ("SMART_BAIT", "ECONOMIC_REEL", "WAN_REEL", "CAROUSEL") and not skip_caption:
        assert caption_engine is not None
        try:
            # Load engagement bait examples from the wonder_feed reference spreadsheet
            # so the LLM can analyse the psychological engagement patterns without
            # copying verbatim.  Returns "" silently if pandas / file is unavailable.
            _bait_examples = (
                _load_engagement_bait_examples(page_ctx)
                if page_ctx and (page_ctx.page_id or "").lower() == "wonder_feed"
                else ""
            )
            # Snapshot the cache at call time so all workers see consistent history.
            _hooks_snapshot: list[str] = list(generated_hooks_cache or [])
            _reject_note = ""
            for _uniq_attempt in range(1, MAX_UNIQUENESS_RETRIES + 1):
                overlay_text, caption, caption_mode_tag, visual_subject = caption_engine.humanize_smart_bait(
                    resolved_subject,
                    page_display_name=page_ctx.display_name if page_ctx else "",
                    page_niche=page_ctx.content_niche if page_ctx else "",
                    cta_enabled=cta_enabled,
                    economic=economic,
                    model_id=econ_model if economic else None,
                    post_type=post_type,
                    engagement_bait_examples=_bait_examples,
                    previously_generated_hooks=_hooks_snapshot,
                    niche_disclaimer=page_ctx.niche_disclaimer if page_ctx else "",
                    narrative_mode=page_ctx.narrative_mode if page_ctx else "",
                    batch_angle_block=_batch_angle_block,
                    uniqueness_rejection=_reject_note,
                    # CAROUSEL: always a long-format caption, never a brand
                    # signature footer (anna_protocol and every carousel post).
                    long_format=(post_type == "CAROUSEL"),
                    forbid_signature=(post_type == "CAROUSEL"),
                )
                _seo_title_local = (
                    getattr(caption_engine, "last_seo_title", "") or ""
                ).strip()
                if uniqueness_guard is None:
                    break
                # Gate BEFORE any paid image/TTS — titles, hooks, caption openings
                _desc_first = (caption or "").strip().splitlines()[0] if caption else ""
                _ok, _prior, _score = uniqueness_guard.try_claim(
                    _seo_title_local,
                    overlay_text,
                    _desc_first,
                )
                if _ok:
                    _LOG.info(
                        "Batch uniqueness OK | variant=%d attempt=%d title=%r",
                        variant + 1, _uniq_attempt, (_seo_title_local or overlay_text)[:80],
                    )
                    break
                _reject_note = uniqueness_guard.rejection_instruction(
                    _prior or "", _score
                )
                _LOG.warning(
                    "Batch uniqueness REJECT | variant=%d attempt=%d sim=%.0f%% prior=%r",
                    variant + 1, _uniq_attempt, _score * 100, (_prior or "")[:80],
                )
                if _uniq_attempt >= MAX_UNIQUENESS_RETRIES:
                    _LOG.error(
                        "Batch uniqueness FAILED after %d attempts | variant=%d — "
                        "proceeding with last draft (may be similar).",
                        MAX_UNIQUENESS_RETRIES, variant + 1,
                    )
                    uniqueness_guard.register(
                        _seo_title_local, overlay_text, _desc_first,
                    )
                # Force visual uniqueness even on retry
                if _batch_angle is not None and _visual_angle_focus:
                    visual_subject = (
                        f"{_visual_angle_focus}. {visual_subject or resolved_subject}"
                    ).strip()
            # Ensure visual_subject carries the angle focus
            if _visual_angle_focus and visual_subject:
                if _visual_angle_focus.lower() not in visual_subject.lower():
                    visual_subject = f"{_visual_angle_focus}. {visual_subject}"
            elif _visual_angle_focus and not visual_subject:
                visual_subject = _visual_angle_focus
        except Exception as exc:  # noqa: BLE001
            _LOG.error("Smart bait generation failed variant %s: %s", variant + 1, exc, exc_info=True)
            logging.error("VARIANT_FAIL | smart_bait | variant=%s | err=%s", variant + 1, exc)
            raise

        # Persist the new hook to the cache so the next variant / run avoids it.
        if overlay_text and hooks_cache_lock is not None and generated_hooks_cache is not None:
            with hooks_cache_lock:
                if overlay_text not in generated_hooks_cache:
                    generated_hooks_cache.append(overlay_text)
                    # Keep the file-based store bounded to the last 60 hooks.
                    if hooks_cache_path is not None:
                        try:
                            import json as _jw
                            _to_save = generated_hooks_cache[-60:]
                            hooks_cache_path.parent.mkdir(parents=True, exist_ok=True)
                            hooks_cache_path.write_text(
                                _jw.dumps(_to_save, ensure_ascii=False, indent=2),
                                encoding="utf-8",
                            )
                            try:
                                from modules.durable_store import sync_state_file

                                sync_state_file(hooks_cache_path)
                            except Exception:
                                pass
                        except Exception as _hwe:  # noqa: BLE001
                            _LOG.warning("Could not persist hooks cache (%s).", _hwe)

        if caption_mode_tag == "researcher_fallback" or (not overlay_text and not caption):
            _LOG.warning(
                "Smart bait generation yielded no usable content for variant %s of '%s'.",
                variant + 1, resolved_subject,
            )

        # Never feed raw caption metaphors into image prompts (anna_protocol
        # "electrical grid" / "adrenal ignition" otherwise become sci-fi art).
        if post_type == "CAROUSEL":
            _pid = (page_ctx.page_id if page_ctx else "").lower()
            visual_subject = ground_image_concept(
                visual_subject or resolved_subject, page_id=_pid,
            ) or visual_subject

    elif post_type == "LONG_CAPTION_IMAGE" and not skip_caption:
        assert caption_engine is not None
        try:
            caption, caption_mode_tag = caption_engine.humanize_long_caption(
                resolved_subject,
                page_display_name=page_ctx.display_name if page_ctx else "",
                page_niche=page_ctx.content_niche if page_ctx else "",
                cta_enabled=cta_enabled,
                cta_keyword=cta_kw if cta_enabled else None,
                economic=economic,
                model_id=econ_model if economic else None,
                signature=page_ctx.caption_signature if page_ctx else "",
            )
        except Exception as exc:  # noqa: BLE001
            _LOG.error("Long caption generation failed variant %s: %s", variant + 1, exc, exc_info=True)
            logging.error("VARIANT_FAIL | long_caption | variant=%s | err=%s", variant + 1, exc)
            raise

        if not caption:
            _LOG.warning(
                "Long caption generation yielded no content for variant %s of '%s'.",
                variant + 1, resolved_subject,
            )

    # ====================================================================
    # MASTER STYLE ROUTING GATE
    # For wonder_feed (and any page with BASE_GRAPHITE_PROMPT configured),
    # SMART_BAIT / LONG_CAPTION_IMAGE / ECONOMIC_REEL / CAROUSEL are hard-locked to the
    # graphite illustration style (no avatar reference image passed to Gemini).
    # graphite illustration pipeline via page_ctx.base_graphite_prompt.
    # The --draw-style CLI flag is SILENTLY IGNORED for these three post types.
    # ====================================================================
    _is_master_mei_page = bool(
        page_ctx and (page_ctx.page_id or "").lower() == "master_mei"
    )
    # Master Mei NEVER uses wonder_feed graphite / pencil-drawing pipelines.
    _graphite_locked = (
        (not _is_master_mei_page)
        and post_type in ("SMART_BAIT", "LONG_CAPTION_IMAGE", "ECONOMIC_REEL", "WAN_REEL", "CAROUSEL")
        and (
            bool(page_ctx and (page_ctx.base_graphite_prompt or page_ctx.sketch_style_prompt))
            or image_style == "SKETCH"   # --draw-style SKETCH always forces graphite pipeline
        )
    )
    if _graphite_locked:
        _LOG.debug(
            "%s | Graphite style lock active (image_style=%s). "
            "Pipeline: BASE_GRAPHITE_PROMPT → build_smart_bait_image_prompt().",
            post_type, image_style,
        )

    effective_atmosphere = atmosphere_style
    # Default before conditional mutation so later debug/logs never hit UnboundLocalError
    # when LONG_CAPTION_IMAGE skips the _base_prompt branch or another post type runs.
    _mutated_subject = resolved_subject or ""
    if (
        (not _is_master_mei_page)
        and post_type in ("SMART_BAIT", "ECONOMIC_REEL", "WAN_REEL")
        and overlay_text
        and caption_engine is not None
    ):
        # visual_subject from LLM takes priority; fall back to keyword extraction
        scene = visual_subject or caption_engine.extract_smart_bait_image_theme(
            overlay_text, economic=economic
        )
        if scene:
            _base_prompt = (
                page_ctx.base_graphite_prompt or page_ctx.sketch_style_prompt
                if page_ctx else ""
            )
            # --draw-style SKETCH override: when no page-level graphite prompt is
            # configured, use the hardcoded sketch directive so the CLI flag
            # always routes Gemini into the charcoal illustration pipeline.
            if not _base_prompt and image_style == "SKETCH":
                _base_prompt = (
                    "in the style of a detailed, emotional charcoal pencil sketch illustration, "
                    "monochrome cross-hatching, moody atmosphere, dark vignette, "
                    "soft horror style couple with expressionless porcelain masks"
                )
            # 35% chance: seamlessly inject one biomechanical horror mutation
            # into the scene description before it reaches the image generator.
            scene = maybe_inject_horror_mutation(scene, probability=0.35)
            effective_atmosphere = build_smart_bait_image_prompt(scene, _base_prompt)
            _LOG.debug("%s | scene (post-mutation): %s", post_type, scene[:120])
    elif post_type == "LONG_CAPTION_IMAGE":
        # Mirror SMART_BAIT exactly: same base prompt, same builder function.
        # resolved_subject is the scene concept; the builder appends it to BASE_GRAPHITE_PROMPT.
        _base_prompt = (
            page_ctx.base_graphite_prompt or page_ctx.sketch_style_prompt
            if page_ctx else ""
        )
        if not _base_prompt and image_style == "SKETCH":
            _base_prompt = (
                "in the style of a detailed, emotional charcoal pencil sketch illustration, "
                "monochrome cross-hatching, moody atmosphere, dark vignette, "
                "soft horror style couple with expressionless porcelain masks"
            )
        if _base_prompt:
            # Horror mutation also fires for LONG_CAPTION_IMAGE at 35% probability
            _mutated_subject = maybe_inject_horror_mutation(resolved_subject, probability=0.35)
            effective_atmosphere = build_smart_bait_image_prompt(
                _mutated_subject, _base_prompt
            )
            _LOG.debug(
                "LONG_CAPTION_IMAGE | scene (post-mutation): %s",
                (_mutated_subject or "")[:120],
            )

    # Batch uniqueness: force angle-specific visual DNA into every image prompt
    if _visual_angle_focus:
        _vf = _visual_angle_focus.rstrip(" .")
        if _vf.lower() not in (effective_atmosphere or "").lower():
            effective_atmosphere = (
                f"{effective_atmosphere.rstrip(' .')}. "
                f"BATCH ANGLE VISUAL FOCUS (unique to this video): {_vf}."
            ).strip()
            _LOG.info(
                "Variant %d | visual angle injected → %r",
                variant + 1, _vf[:80],
            )

    # ====================================================================
    # PHASE A: Image generation
    # Economic mode uses the cheaper image model tier.
    # SMART_BAIT forces avatar_mode="OFF" so the atmospheric context
    # derived from the text hook drives the image prompt unconditionally.
    # ====================================================================
    # SMART_BAIT → atmospheric (avatar OFF).
    # LONG_CAPTION_IMAGE / CTA_CAPTION_IMAGE / CAROUSEL: Gemini Pro + likeness
    # only when --avatar ON is parsed. CAROUSEL honours --avatar ON so the
    # slide frames reuse the page avatar reference (Gemini 3 via bootstrap).
    # Omitted flag keeps cheap Flux (page DEFAULT_AVATAR_MODE does not switch models).
    # ECONOMIC_REEL → OFF by default, EXCEPT master_mei which MUST likeness-lock
    # Act 1 to channels_config/master_mei/avatar_reference/avatar.png.
    if post_type == "SMART_BAIT":
        image_avatar_mode = "OFF"
    elif post_type in ("LONG_CAPTION_IMAGE", "CTA_CAPTION_IMAGE", "CAROUSEL"):
        _cli_av = (
            str((page_ctx.page_cfg.get("AVATAR_CLI") if page_ctx else None) or "")
            .upper()
            .strip()
        )
        image_avatar_mode = _cli_av if _cli_av in ("ON", "OFF") else "OFF"
    elif (
        post_type in ("ECONOMIC_REEL", "WAN_REEL")
        and page_ctx
        and (page_ctx.page_id or "").lower() == "master_mei"
        and not page_ctx.sequence_force_avatar_off
    ):
        image_avatar_mode = "ON"
    elif post_type in ("ECONOMIC_REEL", "WAN_REEL"):
        image_avatar_mode = "OFF"
    else:
        image_avatar_mode = avatar_mode

    # NOTE: A hardcoded generic fallback string ("Dramatic, vivid cinematic wide
    # shot. Emotionally charged abstract environment with bold colours…") used
    # to silently override effective_atmosphere here whenever theme extraction
    # didn't produce anything. REMOVED — every image prompt must come strictly
    # from dynamic LLM generation (visual_subject / theme extraction) + the
    # page's own configured atmosphere_style, never a canned literal string.
    # When theme extraction yields nothing, effective_atmosphere simply keeps
    # its already-assigned value (atmosphere_style, which is itself page-config
    # driven, not a single global hardcoded phrase).

    # ── HARD SKETCH ENFORCEMENT ──────────────────────────────────────────────
    # Final-pass guardian: for every graphite-locked prompt, unconditionally
    # append the charcoal-sketch style directive so Gemini cannot silently
    # drift into photorealism regardless of what the upstream prompt built.
    _SKETCH_LOCK_SUFFIX = (
        " in the style of a detailed, emotional charcoal pencil sketch illustration, "
        "monochrome cross-hatching, moody atmosphere"
    )
    if _graphite_locked and effective_atmosphere and _SKETCH_LOCK_SUFFIX.strip() not in effective_atmosphere:
        effective_atmosphere = effective_atmosphere.rstrip(" .") + _SKETCH_LOCK_SUFFIX
        _LOG.debug("SKETCH LOCK | enforced sketch suffix on effective_atmosphere.")

    # ── WONDER_FEED HORROR-SKETCH OVERRIDE ───────────────────────────────────
    # This block fires LAST in the prompt assembly chain. It:
    #   1. Strips every photorealistic lifestyle / furniture term that leaked
    #      through from ATMOSPHERE_STYLE or fallback strings.
    #   2. When USE_STYLE_REFERENCE is True, builds the prompt by concatenating
    #      ILLUSTRATION_STYLE + STYLE_CHARACTERS from page_config then appends
    #      the soft horror directive. This guarantees the 3-colour sketch palette
    #      and the couple personas are always present in the Gemini payload.
    if page_ctx and (page_ctx.page_id or "").lower() == "wonder_feed":
        _il  = page_ctx.illustration_style.rstrip(" .") if page_ctx.use_style_reference and page_ctx.illustration_style else (
            "Highly detailed pencil sketch style illustration, but restricted to only 3 colors: "
            "dark azure blue, black, and white. Dramatic lighting, expressive. "
            "Clean, minimalist, no messy details."
        )
        _chr = page_ctx.style_characters.rstrip(" .") if page_ctx.use_style_reference and page_ctx.style_characters else (
            "A realistic man and a woman in an intense, dramatic relationship dynamic. "
            "They are the consistent recurring personas."
        )
        _WF_SOFT_HORROR_VARIANTS = [
            (
                f"{_il}. {_chr}. "
                "The man wears a completely plain, expressionless white porcelain-like mask "
                "over his face, while the woman exhibits an intense, raw, anxious expression. "
                "Full-bleed, edge-to-edge canvas portrait, no borders, no bookshelves, no desks, no notebooks."
            ),
            (
                f"{_il}. {_chr}. "
                "The woman is tethered by a dark, fragmented shadow to a looming, faceless male "
                "silhouette fading into subtle ash particles. "
                "Full-bleed, full-screen canvas portrait, no ambient furniture, no clutter."
            ),
            (
                f"{_il}. {_chr}. "
                "The man stands with an eerie, featureless white mask in a heavy vignette shadow, "
                "while the woman stares blankly at a shattered mirror pool on the floor reflecting "
                "her anxiety. Edge-to-edge full bleed, zero margins, no generic room elements."
            ),
            (
                f"{_il}. {_chr}. "
                "The man's silhouette is partially dissolving into fine charcoal dust at the edges "
                "while the woman reaches toward him with trembling hands. Full-screen portrait, "
                "deep vignette shadows, no domestic objects or interior framing."
            ),
            (
                f"{_il}. {_chr}. "
                "Both figures are in close emotional proximity but the man's face is hidden behind "
                "a smooth white porcelain half-mask; the woman's expression is raw and unguarded. "
                "Full-bleed graphite canvas, no borders, no furniture."
            ),
        ]
        import random as _rnd_var
        _WF_SKETCH_PREFIX = _rnd_var.choice(_WF_SOFT_HORROR_VARIANTS) + " "

        _PHOTO_TERMS = [
            "leather armchair", "armchair", "library", "bookshelf", "coffee cup",
            "notebook", "pen", "wooden table", "wooden desk", "cup of tea", "dried flowers",
            "earthenware", "fairy lights", "terracotta", "blush pink", "warm cream", "35mm film",
            "linen curtains", "journaling", "emotionally safe", "aesthetically cozy",
            "cozy", "lifestyle photography", "morning light", "clean surface",
            "window seat", "blanket", "cozy room", "sketchbook", "page", "borders",
            "desk", "table",
            # Block the generic "no human subjects" environmental fallback
            "CINEMATIC ENVIRONMENTAL PHOTOGRAPHY", "NO HUMAN SUBJECTS",
        ]
        _clean = effective_atmosphere
        for _pt in _PHOTO_TERMS:
            _lo = _clean.lower()
            _idx = _lo.find(_pt.lower())
            while _idx != -1:
                _clean = _clean[:_idx] + _clean[_idx + len(_pt):]
                _lo = _clean.lower()
                _idx = _lo.find(_pt.lower())
        import re as _re_mod
        _clean = _re_mod.sub(r"[ \t]{2,}", " ", _clean).strip(" ,.")
        effective_atmosphere = _WF_SKETCH_PREFIX + _clean
        _LOG.info(
            "wonder_feed PROMPT LOCK | use_style_ref=%s | compiled: %s",
            page_ctx.use_style_reference,
            effective_atmosphere[:160],
        )

    # Isolated channels (ancient_knowledge): adapter owns the image prompt.
    # VisualArchitect.build_prompt() returns compose_image_prompt(); the old
    # hardcoded ancient_knowledge lock is not applied. master_mei keeps its lock.
    _mm_image_prompt_override: str = ""
    _isolated_channel = ChannelFactory.is_isolated(
        (page_ctx.page_id or "").lower() if page_ctx else ""
    )
    if (
        page_ctx
        and (page_ctx.page_id or "").lower() == "master_mei"
        and not _isolated_channel
    ):
        import re as _re_mm
        # Act-1 / Hook STRICT MANDATE: ROLE A Master Mei (traditional only).
        # Dynamic environment: ~70% high-altitude nature / ~30% open-air shrines.
        try:
            from agents.media.visual_roles import (
                compose_scene_01_prompt as _mei_s1,
                pick_mei_meditation_environment as _mei_pick_env,
            )
            _hook_env = _mei_pick_env(
                episode_seed=resolved_subject or "",
                spoken_beat=caption if caption and caption != "(skipped)" else "",
            )
            _mm_image_prompt_override = _mei_s1(
                hook_env=_hook_env,
                spoken_beat=caption if caption and caption != "(skipped)" else "",
                subject=resolved_subject or "",
                episode_seed=resolved_subject or "",
            )
        except Exception:
            _hook_env = "towering jagged mountain cliff above a sea of clouds"
            _mm_image_prompt_override = (
                "Master Mei — wise East Asian martial arts grandmaster with pure "
                "snow-white hair in a topknot, two long white locks framing his chest, "
                "extra-long bushy white eyebrows extending past temples, and a long "
                "white beard reaching his mid-chest. White robe and black vest with "
                "gold embroidery. Standing in deep contemplation on a mist-covered "
                "peak cliff, side-profile cinematic wide shot, sea of clouds at "
                "sunrise, mist rising."
            )
        # ROLE A only — traditional Master Mei; never fuse neon city / cybernetics onto him
        _mm_dna_clean = (page_ctx.master_mei_visual_dna or _MEI_DNA_FALLBACK)
        _mm_dna_clean = _re_mm.sub(
            r"\b(?:cyber|bionic|neon|vr|headset|neural|wire|cable|implant|biomechanical)\w*\b",
            "",
            _mm_dna_clean,
            flags=_re_mm.IGNORECASE,
        )
        _mm_dna_clean = _re_mm.sub(r"\s{2,}", " ", _mm_dna_clean).strip(" ,.")
        effective_atmosphere = _mm_image_prompt_override
        _LOG.info(
            "master_mei | purged graphite path | Scene1 FLUX lock: %s",
            _mm_image_prompt_override[:120],
        )

    adapter: GeminiImageAdapter | None = None

    # Per-variant image tracking initialised here so _return_dict always has these keys
    # even when skip_image is True or when image generation raises an exception.
    _carousel_image_paths: list[str] = []
    _carousel_imgbb: list[dict[str, str]] = []
    _images_generated_this_variant: int = 0
    _reused_cover = False
    _topic_lock: dict[str, Any] | None = None
    _topic_gate_extra: int = 0
    # IMAGE_BACKGROUND / IMAGE_QUOTE / IMAGE_AVATAR: call Gemini as normal.
    # ------------------------------------------------------------------
    _is_text_quote = (post_format == "TEXT_QUOTE") and not skip_image

    if _is_text_quote:
        # Resolve canvas dimensions from aspect ratio
        _ratio = (page_aspect_ratio or app_config.GEMINI_IMAGE_ASPECT_RATIO or "3:4").strip()
        _w, _h = 1080, 1350   # default 4:5 portrait
        try:
            _parts = [int(p) for p in _ratio.replace(":", "/").split("/")]
            if len(_parts) == 2 and _parts[0] > 0 and _parts[1] > 0:
                _w = 1080
                _h = int(1080 * _parts[1] / _parts[0])
        except Exception:  # noqa: BLE001
            pass
        try:
            img_path_display = _brand_text_quote_bg(
                (_w, _h),
                page_id=page_ctx.page_id if page_ctx else None,
                output_path=subject_assets / f"{stem}_tq_bg.png",
            )
            logging.info(
                "Variant %s | TEXT_QUOTE_BG_OK | path=%s", variant + 1, img_path_display
            )
        except Exception as exc:  # noqa: BLE001
            _LOG.error(
                "TEXT_QUOTE background generation failed variant %s: %s",
                variant + 1, exc, exc_info=True,
            )
            raise

    elif not skip_image:
        architect = VisualArchitect(channel=ChannelFactory.from_env())
        image_prompt = architect.build_prompt(
            resolved_subject,
            variation_index=variation_index,
            total_variants=qty,
            avatar_mode=image_avatar_mode,
            atmosphere_style=effective_atmosphere,
            aspect_ratio=page_aspect_ratio or None,
            style=image_style,
        )
        # ── WONDER_FEED STYLE LOCK: hard-replace the final prompt ────────────
        # Uses the exact constants from page_config.py so the 3-colour palette
        # and couple personas are always present — architect output is discarded
        # and replaced with the explicit structure the image engine requires.
        if (
            page_ctx
            and (page_ctx.page_id or "").lower() == "wonder_feed"
            and page_ctx.use_style_reference
        ):
            _wf_il = (
                page_ctx.illustration_style.rstrip(" .")
                if page_ctx.illustration_style
                else (
                    "Highly detailed pencil sketch style illustration, but restricted to only "
                    "3 colors: dark azure blue, black, and white. Dramatic lighting, expressive. "
                    "Clean, minimalist, no messy details."
                )
            )
            _wf_ch = (
                page_ctx.style_characters.rstrip(" .")
                if page_ctx.style_characters
                else (
                    "A realistic man and a woman in an intense, dramatic relationship dynamic. "
                    "They are the consistent recurring personas."
                )
            )
            _scene_desc = (visual_subject or resolved_subject).strip(" .")
            # Sanitize scene description: strip furniture/book/domestic terms
            # so the image engine cannot hallucinate room objects from the text.
            _SCENE_JUNK = [
                "bookshelf", "bookshelves", "bookcase", "shelf", "open book", "open books",
                "coffee cup", "cup of coffee", "coffee table", "armchair", "armchairs",
                "leather chair", "desk", "office desk", "wooden desk", "window", "windowsill",
                "window seat", "library", "reading room", "sofa", "couch", "lamp",
                "notebook", "journal", "pen", "pencil on paper", "table", "kitchen",
                "bedroom", "living room", "curtain", "curtains",
            ]
            import re as _re_scene
            _sd = _scene_desc
            for _jt in _SCENE_JUNK:
                _lo = _sd.lower()
                _ix = _lo.find(_jt.lower())
                while _ix != -1:
                    _sd = _sd[:_ix] + _sd[_ix + len(_jt):]
                    _lo = _sd.lower()
                    _ix = _lo.find(_jt.lower())
            _scene_desc = _re_scene.sub(r"[ \t]{2,}", " ", _sd).strip(" ,.")
            image_prompt = (
                f"{_wf_il}. {_wf_ch}. "
                f"Scene Concept: {_scene_desc}. "
                "CRITICAL COMPLIANCE: Do NOT draw any objects from the text literal. "
                "Absolutely NO bookshelves, NO open books, NO coffee cups, NO desks, "
                "NO armchairs, NO windows. "
                "The frame must only feature the expressive dark azure pencil art couple. "
                "Full-bleed, full-screen canvas."
            )
            _LOG.info(
                "wonder_feed IMAGE PROMPT LOCK | hard-override applied | scene: %s",
                _scene_desc[:80],
            )

        # Isolated adapter already composed the prompt inside VisualArchitect.
        if _isolated_channel:
            _LOG.info(
                "%s IMAGE PROMPT | adapter compose_image_prompt: %s",
                (page_ctx.page_id if page_ctx else "isolated"),
                image_prompt[:180],
            )
        elif _mm_image_prompt_override:
            image_prompt = _mm_image_prompt_override
            _LOG.info(
                "master_mei IMAGE PROMPT LOCK | hard-override applied: %s",
                image_prompt[:180],
            )
        if post_type in ("LONG_CAPTION_IMAGE", "CAROUSEL") and not _mm_image_prompt_override:
            from agents.media.scene_prompt_generator import (
                apply_topic_visual_lock as _tvl,
            )

            _topic_lock = _tvl(
                image_prompt,
                topic=resolved_subject or "",
                caption=f"{visual_subject or ''} {caption or overlay_text or ''}",
                style=effective_atmosphere or "",
            )
            image_prompt = str(_topic_lock.get("image_generation_prompt") or image_prompt)
            _LOG.info(
                "%s TOPIC LOCK | domain=%s banned=%s | prompt=%s",
                post_type,
                _topic_lock.get("domain_id") or "(none)",
                _topic_lock.get("banned_subjects"),
                image_prompt[:160],
            )
            print(
                f"[TOPIC-LOCK] {post_type} domain={_topic_lock.get('domain_id') or '(none)'} "
                f"banned={_topic_lock.get('banned_subjects')} "
                f"prompt={image_prompt[:180]}",
                flush=True,
            )
        img_model_id = None
        _page_cost = page_ctx.cost_tier if page_ctx is not None else None
        # Precedence: --image-primary / page IMAGE_PRIMARY (Gemini SKUs) >
        # explicit --img-production > page IMAGE_MODEL_OVERRIDE >
        # explicit Together flow > router default.
        _override = None
        if page_ctx is not None and page_ctx.image_model_override:
            _override = page_ctx.image_model_override
        try:
            from agents.mcp.model_api_flows import (  # noqa: PLC0415
                get_active_flow as _get_flow,
                is_flow_explicitly_set as _flow_set,
                resolved_together_image_model as _flow_img_model,
            )
            import config as _img_cfg  # noqa: PLC0415

            _flow = _get_flow()
            _cli_primary = bool(
                page_ctx and page_ctx.page_cfg.get("IMAGE_PRIMARY_CLI")
            )
            _cli_img_prod = bool(
                page_ctx and page_ctx.page_cfg.get("IMG_PRODUCTION_CLI")
            )
            if _flow_set() and _flow is not None and not _cli_primary:
                _prov = getattr(_flow.image, "provider", "") or ""
                if _prov == "gemini" and _flow.image.model:
                    _override = _flow.image.model
                elif _cli_img_prod and _prov in ("together", "deepinfra"):
                    _flow_model = _flow_img_model()
                    if _flow_model:
                        _override = _flow_model
                elif not _img_cfg.is_gemini_image_model(_override):
                    _flow_model = _flow_img_model()
                    if _flow_model:
                        _override = _flow_model
        except Exception:
            pass
        from agents.media.providers.model_router import image_model as _route_image
        _img_route = _route_image(
            task="image",
            page_cost_tier=_page_cost,
            model_override=_override,
            preferred=(
                app_config.GEMINI_ECONOMIC_IMAGE_MODEL
                if economic
                else app_config.GEMINI_IMAGE_MODEL
            ),
            use_premium=True if (_page_cost or "").lower() == "premium" else None,
            log=True,
        )
        img_model_id = app_config.normalize_image_model_id(_img_route.model_id)
        if economic or _img_route.tier == "cheap":
            _LOG.info(
                "COST-FIRST LOCK | variant=%s | tier=%s | image_model=%s | text_brain=%s",
                variant + 1,
                _img_route.tier,
                img_model_id,
                app_config.GEMINI_ECONOMIC_BRAIN_MODEL,
            )
        # Resolve style reference image.
        # For wonder_feed the reference file is pinned to an absolute path so
        # that STYLE_REFERENCE_DIR misconfigs or directory-scan order can never
        # cause the wrong image (or no image) to be sent to Gemini.
        import os as _os
        from utils.pipeline_paths import WINDOWS_FACTORY_ROOT as _WINDOWS_FACTORY_ROOT

        _wf_style_name = "Screenshot 2026-06-01 183244.png"
        _wf_style_env = (_os.getenv("WONDER_FEED_STYLE_REFERENCE") or "").strip().strip('"').strip("'")
        _wf_style_repo = (
            REPO_ROOT
            / "channels_config"
            / "wonder_feed"
            / "style_reference"
            / _wf_style_name
        )
        _wf_style_windows = (
            _WINDOWS_FACTORY_ROOT
            / "channels_config"
            / "wonder_feed"
            / "style_reference"
            / _wf_style_name
        )
        if _wf_style_env:
            _WF_STYLE_REF = Path(_wf_style_env).expanduser()
        elif _wf_style_windows.is_file():
            _WF_STYLE_REF = _wf_style_windows
        else:
            _WF_STYLE_REF = _wf_style_repo
        _WF_STYLE_REF_STR = str(_WF_STYLE_REF)
        _style_ref_path: Path | None = None
        _style_ref_paths: list[Path] = []
        _style_ref_weight: float = 0.72
        if page_ctx and (page_ctx.page_id or "").lower() == "wonder_feed":
            print(f"[DEBUG_STYLE_ENGINE] Target image reference assigned: {_WF_STYLE_REF_STR}")
            if _os.path.exists(_WF_STYLE_REF_STR):
                _style_ref_path = _WF_STYLE_REF
                _style_ref_paths = [_WF_STYLE_REF]
                print("[DEBUG_STYLE_ENGINE] Verification PASSED — style reference loaded.")
                logging.info("wonder_feed STYLE REF LOCK | pinned → %s", _WF_STYLE_REF.name)
            else:
                print(f"[ERROR_STYLE_ENGINE] Verification Failed. Path missing: {_WF_STYLE_REF_STR}")
                logging.warning(
                    "wonder_feed style reference not found at expected path: %s", _WF_STYLE_REF
                )
        elif page_ctx:
            # MODULE 3 — dynamic per-page resolution: channels_config/<page>/style_reference/
            # (or explicit STYLE_REFERENCE_DIR override), 2-3 images, IP-Adapter-style weight.
            _style_ref_paths = page_ctx.resolve_style_reference_images()
            _style_ref_weight = page_ctx.style_reference_weight
            if _style_ref_paths:
                _style_ref_path = _style_ref_paths[0]

        try:
            import os as _img_os
            print("\n" + "=" * 60)
            print("[DEBUG] IMAGE PIPELINE INITIALIZATION")
            print(f"[DEBUG] Model            : {img_model_id or bm.image_primary_id}")
            if page_ctx and (page_ctx.page_id or "").lower() == "wonder_feed":
                _dbg_ref = _WF_STYLE_REF_STR
                print(f"[DEBUG] Style Ref Path   : {_dbg_ref}")
                print(f"[DEBUG] Ref File Exists  : {_img_os.path.exists(_dbg_ref)}")
            else:
                print(f"[DEBUG] Style Ref Path   : {_style_ref_path or '(none — text-only prompt)'}")
            print(f"[DEBUG] Compiled prompt  : {effective_atmosphere[:200]}")
            print("=" * 60 + "\n")
            # Routes to RemoteGPUImageAdapter when ENABLE_REMOTE_GPU_WORKFLOWS=true
            adapter = get_image_adapter(
                model_id=img_model_id,
                page_cost_tier=_page_cost,
                tier=_img_route.tier,
            )
            if page_ctx is not None:
                from agents.media.free_images.integrate import wrap_image_generate

                _free_producer = (
                    "reel_image"
                    if post_type in ("ECONOMIC_REEL", "WAN_REEL")
                    else "facebook_card"
                )
                adapter.generate = wrap_image_generate(
                    adapter.generate,
                    channel=page_ctx.page_id,
                    page_cfg=page_ctx.page_cfg,
                    query=resolved_subject or visual_subject or "",
                    producer=_free_producer,
                )
            # master_mei: always prefer forced avatar.png over cycled assets
            _gen_ref = effective_ref_path if image_avatar_mode == "ON" else None
            _gen_weight: float | None = None
            if (
                page_ctx
                and (page_ctx.page_id or "").lower() == "master_mei"
                and image_avatar_mode == "ON"
            ):
                _forced = page_ctx.forced_avatar_reference_path
                if _forced is not None and _forced.is_file():
                    _gen_ref = _forced
                _gen_weight = page_ctx.avatar_image_weight
            _act1_role = None
            _act1_neg = None
            if page_ctx and (page_ctx.page_id or "").lower() == "master_mei":
                from agents.media.visual_roles import (
                    ROLE_A_NEGATIVE,
                    ROLE_MASTER,
                )
                _act1_role = ROLE_MASTER
                _act1_neg = ROLE_A_NEGATIVE
            if post_type in _SEQUENCE_VIDEO_POST_TYPES:
                _cover_hit = _find_cached_sequence_still(
                    1,
                    work_dir=subject_assets,
                    stem=stem,
                )
                if _cover_hit is not None:
                    img_path_display = _cover_hit
                    _reused_cover = True
                    _LOG.info(
                        "IMAGE CACHE HIT | skipping cover generate — reusing %s",
                        _cover_hit,
                    )
            # Audio-first: never spend an image call before TTS timestamps exist.
            if (
                page_ctx
                and page_ctx.enable_sequence_reel
                and post_type in _SEQUENCE_VIDEO_POST_TYPES
                and not _reused_cover
            ):
                _LOG.info(
                    "AUDIO-FIRST | deferring stills until TTS word timestamps are ready"
                )
                img_path_display = None
                _reused_cover = True
            if not _reused_cover:
                _still_neg = _act1_neg
                if _topic_lock and _topic_lock.get("negative_prompt"):
                    _still_neg = (
                        f"{_act1_neg}, {_topic_lock['negative_prompt']}"
                        if _act1_neg
                        else str(_topic_lock["negative_prompt"])
                    )
                _gen_kwargs = {
                    "reference_image_path": _gen_ref,
                    "style_reference_path": _style_ref_path,
                    "style_reference_paths": _style_ref_paths or None,
                    "style_reference_weight": _style_ref_weight,
                    "output_directory": subject_assets,
                    "avatar_mode": image_avatar_mode,
                    "reference_image_weight": _gen_weight,
                    "visual_role": _act1_role,
                    "negative_prompt": _still_neg,
                }
                if post_type in ("LONG_CAPTION_IMAGE", "CAROUSEL"):
                    from modules.reel_visual_qa import generate_and_gate as _still_gate

                    img_path_display, _topic_gate_extra = _still_gate(
                        adapter.generate,
                        prompt=image_prompt,
                        chunk_text=resolved_subject or visual_subject or "",
                        output_stem=stem,
                        output_directory=subject_assets,
                        channel=(page_ctx.page_id if page_ctx else "ancient_knowledge"),
                        search_dirs=[subject_assets] if subject_assets else [],
                        generate_kwargs=_gen_kwargs,
                        banned_subjects=list(_topic_lock.get("banned_subjects") or [])
                        if _topic_lock
                        else None,
                        domain_anchors=str(
                            (_topic_lock or {}).get("domain_anchors") or ""
                        ),
                        prefer_stem=stem,
                    )
                else:
                    img_path_display = adapter.generate(
                        image_prompt,
                        output_stem=stem,
                        **_gen_kwargs,
                    )
            img_used = adapter.last_gemini_image_model_used or bm.image_primary_id
            logging.info(
                "Variant %s | IMAGE_OK | model_used=%s | path=%s",
                variant + 1,
                img_used,
                img_path_display,
            )
        except Exception as exc:  # noqa: BLE001
            failed_mid = img_model_id or bm.image_primary_id
            if adapter is not None:
                failed_mid = adapter.last_gemini_image_failure_model_id or failed_mid
            _LOG.error(
                "Image generation failed variant %s attempted_model=`%s`",
                variant + 1,
                failed_mid,
                exc_info=True,
            )
            logging.error(
                "VARIANT_FAIL | GeminiImageAdapter | variant=%s | model=`%s` | err=%s",
                variant + 1,
                failed_mid,
                exc,
            )
            raise

        # ECONOMIC_REEL has its own full-length video compilation in Phase D.
        # Skip the 7-second image-loop conversion so it doesn't overwrite
        # video_path_str with a redundant short clip before the reel is built.
        if (
            isinstance(img_path_display, Path)
            and img_path_display.is_file()
            and post_type not in ("ECONOMIC_REEL", "WAN_REEL")
        ):
            video_path_str = _maybe_convert_to_video(
                img_path_display,
                duration=7,
                page_ctx=page_ctx,
            )

    # ---- Raw background reference (Gemini output, always textless) --------
    raw_bg_path: Path | None = (
        img_path_display if isinstance(img_path_display, Path) else None
    )
    img_ref_engine = ""
    if raw_bg_path is not None:
        img_ref_engine = path_under_engine(app_config.ENGINE_ROOT, raw_bg_path)

    # ── COST TRACKING: image generation ──────────────────────────────────────
    # For CAROUSEL the first generate() call above is Slide 01.
    # The Slide 02 and 03 generation loop below tracks each separately.
    # Charge real API hits (retries / chain advances), not just successful frames.
    if not skip_image and not _reused_cover:
        _n_hits = _track_adapter_image(cost_tracker, adapter) if adapter is not None else 1
        _images_generated_this_variant = _n_hits
        if cost_tracker is None:
            _images_generated_this_variant = 1
        _images_generated_this_variant += max(0, int(_topic_gate_extra or 0))
    elif _reused_cover:
        _LOG.info("IMAGE CACHE | cover billed as $0 (reused still, no API this run)")

    # ── CAROUSEL: generate slides 2..N with distinct viewpoint directives ─
    # Slide 1 is the image already generated above (image_prompt).
    # Remaining slides do NOT reuse the same scene: each slide illustrates the
    # carousel topic from a DIFFERENT original angle/facet, so the frames read
    # as a real multi-scene narrative and are never repetitive copies of slide 01.
    # --carousel_quantity controls how many images each carousel hosts (default 3);
    # --quantity controls how many carousels are produced (handled upstream).
    if post_type == "CAROUSEL" and not skip_image and raw_bg_path is not None and adapter is not None:
        _carousel_quantity = max(1, int(carousel_quantity or 3))
        _carousel_image_paths.append(str(raw_bg_path))  # slide 01
        # Facets only — never send "SLIDE 02 — …" labels to the image model
        # (those strings were being painted onto the canvas as typography).
        _carousel_slide_facets = [
            "Human scale: hands preparing the remedy, a figure at a wooden table, "
            "or someone gathering the plant — a different moment than slide 01.",
            "The living landscape that produces this subject: garden, shoreline, "
            "forest floor, or kitchen garden at golden hour. Wide environmental frame.",
            "Macro of the real material — salt crystals, dried leaves, oil sheen, "
            "wood grain, steam — fill the frame with texture, not a diagram.",
            "Overhead still-life layout of the ingredients and tools on linen or "
            "cedar, arranged as a photograph not an infographic.",
            "The ritual in motion: pouring, stirring, harvesting, or steeping. "
            "Action and change in the frame.",
            "Mood and atmosphere only: silhouette, steam, window light, and "
            "negative space. No frontal product-shot repeat of slide 01.",
            "The daily-life connection: the finished cup, jar, or meal in a real "
            "kitchen or table setting, warm and lived-in.",
        ]
        _pid = (page_ctx.page_id if page_ctx else "").lower()
        for _ci in range(1, _carousel_quantity):
            _slide_num = _ci + 1  # 2, 3, … carousel_quantity
            _facet = _carousel_slide_facets[(_ci - 1) % len(_carousel_slide_facets)]
            _slide_prompt = build_carousel_slide_prompt(
                topic=resolved_subject,
                visual_subject=visual_subject or "",
                facet=_facet,
                slide_num=_slide_num,
                total=_carousel_quantity,
                page_id=_pid,
                atmosphere_style=effective_atmosphere or atmosphere_style or "",
            )
            _slide_lock = None
            try:
                from agents.media.scene_prompt_generator import (
                    apply_topic_visual_lock as _slide_tvl,
                )

                _slide_lock = _slide_tvl(
                    _slide_prompt,
                    topic=resolved_subject or "",
                    caption=f"{visual_subject or ''} {_facet}",
                    style=effective_atmosphere or "",
                )
                _slide_prompt = str(
                    _slide_lock.get("image_generation_prompt") or _slide_prompt
                )
            except Exception:
                _slide_lock = None
            try:
                from modules.reel_visual_qa import generate_and_gate as _slide_gate

                _slide_img, _slide_extra = _slide_gate(
                    adapter.generate,
                    prompt=_slide_prompt,
                    chunk_text=f"{resolved_subject} {_facet}",
                    output_stem=f"{stem}_slide_{_slide_num:02d}",
                    output_directory=subject_assets,
                    channel=_pid or "ancient_knowledge",
                    search_dirs=[subject_assets] if subject_assets else [],
                    generate_kwargs={
                        "reference_image_path": (
                            effective_ref_path if image_avatar_mode == "ON" else None
                        ),
                        "avatar_mode": image_avatar_mode,
                        "negative_prompt": (
                            str(_slide_lock.get("negative_prompt") or "")
                            if _slide_lock
                            else None
                        ),
                    },
                    banned_subjects=list(_slide_lock.get("banned_subjects") or [])
                    if _slide_lock
                    else None,
                    domain_anchors=str(
                        (_slide_lock or {}).get("domain_anchors") or ""
                    ),
                    prefer_stem=stem,
                )
                _carousel_image_paths.append(str(_slide_img))
                _images_generated_this_variant += _track_adapter_image(cost_tracker, adapter)
                _images_generated_this_variant += max(0, int(_slide_extra or 0))
                _LOG.info(
                    "CAROUSEL slide %d/%d generated → %s",
                    _slide_num, _carousel_quantity, Path(_slide_img).name,
                )
                print(
                    f"[TOPIC-LOCK] CAROUSEL slide {_slide_num}/{_carousel_quantity} "
                    f"domain={(_slide_lock or {}).get('domain_id') or '(none)'} "
                    f"banned={(_slide_lock or {}).get('banned_subjects')}",
                    flush=True,
                )
            except Exception as _ce:  # noqa: BLE001
                _LOG.warning("CAROUSEL slide %d generation failed: %s — skipping.", _slide_num, _ce)
    elif post_type == "CAROUSEL" and not skip_image and raw_bg_path is not None:
        _carousel_image_paths.append(str(raw_bg_path))

    # ── SEQUENCE REEL: dense ~4 s/act scene-to-audio sync ─────────────────
    # Early voiceover script → segment into N spoken snippets → one Gemini
    # image per snippet → Phase D TTS + compile (act_dur = audio / N).
    _sequence_image_paths: list = []
    _early_music_thread = None
    _early_music_box: dict = {}
    _early_seq_script: str = ""
    _ak_voice_ready = False
    _voice_path = None
    _narr_dur = 0.0
    _narr_word_timings: list = []
    _ak_act_durs: "list[float] | None" = None
    _sentence_starts: list = []
    _timeline_audio = 0.0
    _asset_pool: list = []
    _asset_assignments: list = []
    _pool_geography = ""
    _beat_qa_rows: list = []
    _reel_qa_block_compile = False
    _image_bill: dict[str, int] = {}
    try:
        from modules.reel_artefact.budget import reset_nano_budget as _reset_nano
        from modules.reel_artefact.budget import reset_spend as _reset_spend
        from modules.reel_artefact.budget import reset_uses as _reset_uses
        from modules.reel_artefact.checkpoint import mark_tts_resumed as _mark_tts
        from modules.reel_artefact.diversity import reset_diversity as _reset_diverse
        from modules.reel_artefact.logbook import reset as _reset_sources
        from modules.reel_artefact.asset_pool import reset_claims as _reset_claims
        from modules.reel_artefact.reuse import reset_approved as _reset_approved

        _reset_claims()
        _reset_nano()
        _reset_spend()
        _reset_uses()
        _reset_diverse()
        _reset_sources()
        _reset_approved()
        _mark_tts(False)
    except Exception:  # noqa: BLE001
        pass
    try:
        from modules.reel_artefact.scene_library import start_reel as _scene_lib_start

        _scene_lib_start(
            (page_ctx.page_id if page_ctx else "") or "ancient_knowledge"
        )
    except Exception:  # noqa: BLE001
        pass
    _qa_report: dict | None = None
    # Episode assets: outputs/<page>/assets/<episode_id>/
    # Clips always:   outputs/<page>/clips/
    _episode_id = f"ep_{datetime.now().strftime('%Y%m%d_%H%M')}"
    _episode_assets_dir: Path | None = None
    if post_type in ("ECONOMIC_REEL", "WAN_REEL"):
        _episode_assets_dir = Path(app_config.ASSETS_DIR) / _episode_id
        _episode_assets_dir.mkdir(parents=True, exist_ok=True)
        Path(app_config.PAGE_OUTPUTS_DIR, "clips").mkdir(parents=True, exist_ok=True)
        _LOG.info("Episode assets directory → %s", _episode_assets_dir.resolve())
    if (
        page_ctx
        and page_ctx.enable_sequence_reel
        and post_type in ("ECONOMIC_REEL", "WAN_REEL")
        and adapter is not None
    ):
        _ak_base_style = (
            page_ctx.illustration_style.rstrip(" .")
            if page_ctx.illustration_style
            else page_ctx.atmosphere_style.rstrip(" .")
        )
        _is_mm = (page_ctx.page_id or "").lower() == "master_mei"
        _seq_words = page_ctx.reel_narration_words if page_ctx else app_config.words_for_duration(80.0)
        _seq_min = page_ctx.reel_narration_min_words if page_ctx else app_config.words_for_duration(80.0)

        # Master Mei: duration profile locks words + frame count (7/9/10).
        # Other pages: shared plan_scenes() engine (or legacy dense/hook).
        _planned_scene_durs: "list[float] | None" = None
        if _is_mm:
            from agents.media.mei_narrative import resolve_mei_duration_profile

            _mei_prof = resolve_mei_duration_profile(page_ctx.reel_duration)
            _seq_n = int(_mei_prof["frames"])
            _seq_words = int(_mei_prof["words_target"])
            _seq_min = int(_mei_prof["words_min"])
            _LOG.info(
                "Master Mei duration profile | target=%ss words=%d–%d (tgt %d) frames=%d "
                "max_consec_training=%d",
                _mei_prof["target_s"],
                _mei_prof["words_min"],
                _mei_prof["words_max"],
                _mei_prof["words_target"],
                _mei_prof["frames"],
                _mei_prof["max_consec_training"],
            )
        else:
            # Two-tier act count for long videos (Final Round 2026-08-15).
            # ancient_knowledge opts in via USE_TWO_TIER_PACING=True:
            # Tier 1 = first ~90 s narration @ ~4.5 s/still, capped at 20
            # acts (fast-cut opening). Tier 2 = remaining narration at
            # ~10 s/still (slower body, 8-12 s clamped). No arbitrary
            # ceiling on total acts — a 300 s reel produces ~39 stills.
            _tier1_body_target: "int | None" = None  # → passed to plan_bucket_act_durations
            if post_type == "WAN_REEL":
                # Isolated from ECONOMIC_REEL two-tier / progressive still pacing.
                # page_config WAN_SCENE_DURATION=fixed:7 → ~10–12 longer clips.
                from core.scene_pacing import (
                    parse_scene_duration as _parse_wan_scene_dur,
                )

                _wan_spec = _parse_wan_scene_dur(page_ctx.wan_scene_duration)
                _wan_hold = (
                    float(_wan_spec.fixed_s)
                    if _wan_spec.mode == "fixed" and float(_wan_spec.fixed_s) > 0
                    else 7.0
                )
                _wan_dur = float(
                    getattr(page_ctx, "wan_reel_duration", None)
                    or page_ctx.reel_duration
                    or 70.0
                )
                _seq_n = max(8, min(14, int(round(_wan_dur / _wan_hold))))
                _planned_scene_durs = [_wan_hold] * _seq_n
                _LOG.info(
                    "WAN_REEL scene pacing | %s hold=%.1fs duration=%.1fs → %d acts "
                    "(ECONOMIC_REEL two-tier unused)",
                    page_ctx.wan_scene_duration, _wan_hold, _wan_dur, _seq_n,
                )
            elif getattr(page_ctx, "use_two_tier_pacing", False):
                from core.reel_sequence_engine import (
                    compute_two_tier_act_count as _compute_two_tier_act_count,
                )
                _tier1_n, _tier2_n, _seq_n = _compute_two_tier_act_count(
                    page_ctx.reel_duration,
                    tier1_max_acts=page_ctx.reel_tier1_max_acts,
                    tier1_horizon_s=page_ctx.reel_tier1_horizon_s,
                    tier1_seconds_per_act=page_ctx.reel_tier1_seconds_per_act,
                    tier2_seconds_per_act=page_ctx.reel_tier2_seconds_per_act,
                    min_acts=page_ctx.reel_image_min_count,
                )
                _tier1_body_target = max(0, _tier1_n - 3)  # tier-1 body = tier1_n minus hook_n(3)
                _LOG.info(
                    "TWO-TIER PACING | duration=%.1fs → tier1=%d acts (hook 3 + body %d) "
                    "+ tier2=%d acts (body 8-12s) = %d total | tier1_horizon=%.0fs "
                    "tier1_spa=%.1fs tier2_spa=%.1fs (no static ceiling)",
                    page_ctx.reel_duration,
                    _tier1_n, _tier1_body_target,
                    _tier2_n, _seq_n,
                    page_ctx.reel_tier1_horizon_s,
                    page_ctx.reel_tier1_seconds_per_act,
                    page_ctx.reel_tier2_seconds_per_act,
                )
            elif page_ctx.uses_plan_scenes_pacing:
                _planned_scene_durs = _plan_scenes(
                    page_ctx.reel_duration,
                    page_ctx.scene_duration,
                    progressive_start_s=page_ctx.scene_progressive_start_s,
                    progressive_step_every=page_ctx.scene_progressive_step_every,
                    progressive_step_s=page_ctx.scene_progressive_step_s,
                    progressive_cap_s=page_ctx.scene_progressive_cap_s,
                    min_scenes=page_ctx.reel_image_min_count,
                    max_scenes=page_ctx.reel_image_count,
                )
                _seq_n = len(_planned_scene_durs)
            elif page_ctx.reel_use_hook_body_pacing:
                _seq_n = _compute_hook_body_act_count(
                    page_ctx.reel_duration,
                    hook_hold_s=page_ctx.reel_hook_hold_s,
                    body_hold_s=page_ctx.reel_body_hold_s,
                    min_acts=page_ctx.reel_image_min_count,
                    max_acts=page_ctx.reel_image_count,
                )
            else:
                _seq_n = _compute_dense_act_count(
                    page_ctx.reel_duration,
                    seconds_per_act=page_ctx.reel_seconds_per_act,
                    min_acts=page_ctx.reel_image_min_count,
                    max_acts=page_ctx.reel_image_count,
                )
            _scene_len = page_ctx.scene_length
            if (
                _scene_len
                and post_type != "WAN_REEL"
                and not getattr(page_ctx, "use_two_tier_pacing", False)
            ):
                # SCENE_LENGTH override — legacy fixed-cadence path. Two-tier
                # pacing IGNORES this override on purpose so long-video Tier
                # 2 acts stay at their 10 s cadence instead of being clipped
                # by a channel-level SCENE_LENGTH constant.
                _seq_n = max(
                    page_ctx.reel_image_min_count,
                    min(
                        page_ctx.reel_image_count,
                        max(2, int(round(page_ctx.reel_duration / float(_scene_len)))),
                    ),
                )
                _LOG.info(
                    "SCENE_LENGTH override | %.1fs/still → %d acts",
                    float(_scene_len), _seq_n,
                )

        # ── Work dir under episode assets (final scene_XX.png copies land here) ──
        assert _episode_assets_dir is not None
        _reel_img_dir = _episode_assets_dir / "work"
        _reel_img_dir.mkdir(parents=True, exist_ok=True)
        if _is_mm:
            _pace_label = "mei_profile"
        elif post_type == "WAN_REEL":
            _pace_label = (
                f"wan_fixed:{page_ctx.wan_scene_duration} n={_seq_n}"
            )
        elif getattr(page_ctx, "use_two_tier_pacing", False):
            _pace_label = (
                f"two-tier tier1={locals().get('_tier1_n', 0)}@{page_ctx.reel_tier1_seconds_per_act:.1f}s "
                f"tier2={locals().get('_tier2_n', 0)}@{page_ctx.reel_tier2_seconds_per_act:.1f}s"
            )
        elif locals().get("_planned_scene_durs"):
            _pace_label = (
                f"plan_scenes:{page_ctx.scene_duration} | "
                f"{_describe_scene_plan(_planned_scene_durs)}"
            )
        elif page_ctx.reel_use_hook_body_pacing:
            _pace_label = (
                f"hook={page_ctx.reel_hook_hold_s:.1f}s "
                f"body={page_ctx.reel_body_hold_s:.1f}s"
            )
        else:
            _pace_label = f"dense={page_ctx.reel_seconds_per_act:.1f}s/act"
        _LOG.info(
            "Sequence reel | acts=%d | video_length=%.0fs (window %.0f–%.0fs) | "
            "pacing=%s | episode=%s | work=%s",
            _seq_n,
            page_ctx.reel_duration,
            page_ctx.reel_duration_target_min,
            page_ctx.reel_duration_target_max,
            _pace_label,
            _episode_id,
            _reel_img_dir,
        )

        # Early script for ALL sequence pages → exact spoken-beat image prompts.
        # Uniqueness gate rejects near-duplicate scripts BEFORE any act images / TTS
        # (that loop stays — uniqueness is a separate axis from duration).
        #
        # Round 7 (Measure-Then-Correct): the old "early script short →
        # regenerate longer" inner retry has been REMOVED. Word count from
        # a single Gemini call is now treated as a seed hint, not a gate —
        # ``_synthesize_sequence_voice_track`` downstream measures the
        # ACTUAL TTS duration and issues at most one corrective regen using
        # observed WPS. Chasing word count here without any TTS
        # measurement was the source of most of the extra Gemini calls
        # seen in the 10-reel batch log.
        if caption_engine is not None:
            try:
                if (
                    page_ctx
                    and (page_ctx.page_id or "").lower() == "ancient_knowledge"
                ):
                    from modules.reel_artefact.policy import (
                        artefact_images_enabled as _pool_flag,
                    )

                    if _pool_flag():
                        from modules.reel_artefact.asset_pool import (
                            build_topic_pool as _build_topic_pool,
                        )

                        _asset_pool = _build_topic_pool(
                            str(resolved_subject or ""),
                            "ancient_knowledge",
                            _reel_img_dir / "pool",
                        )
                        if not _asset_pool:
                            from modules.reel_artefact.asset_pool import (
                                PoolTooSmall as _PoolTooSmall,
                            )

                            raise _PoolTooSmall(
                                "asset pool empty after cache, search, and nano"
                            )
                        _LOG.info(
                            "ASSET POOL | n=%d | %s",
                            len(_asset_pool),
                            " | ".join(
                                str(getattr(item, "caption", "") or "")[:40]
                                for item in _asset_pool[:6]
                            ),
                        )
                        try:
                            from modules.shot_planner.entities import (
                                entity_for_line as _pool_entity,
                            )
                            from modules.shot_planner.entities import (
                                load_entities as _pool_entities,
                            )

                            _pool_rows = _pool_entities("ancient_knowledge")
                            _pool_ent = _pool_entity(
                                str(resolved_subject or ""),
                                _pool_rows,
                                topic=str(resolved_subject or ""),
                            )
                            _pool_geography = (
                                _pool_ent.geography if _pool_ent is not None else ""
                            )
                        except Exception:  # noqa: BLE001
                            _pool_geography = ""
                _seq_reject = ""
                _hooks_for_seq = list(generated_hooks_cache or [])
                _asset_captions = [
                    str(getattr(item, "caption", "") or "")
                    for item in _asset_pool
                    if str(getattr(item, "caption", "") or "").strip()
                ]
                for _seq_attempt in range(1, MAX_UNIQUENESS_RETRIES + 1):
                    _early_seq_script = caption_engine.generate_sequence_voiceover(
                        resolved_subject,
                        page_niche=page_ctx.content_niche,
                        persona_voice=page_ctx.tts_voice_preference or (
                            "calm, deep, highly wise, authoritative ancient master"
                            if _is_mm
                            else "investigative, neutral, immersive"
                        ),
                        n_acts=_seq_n,
                        duration_s=page_ctx.reel_duration,
                        total_words_target=_seq_words,
                        economic=economic,
                        niche_disclaimer=page_ctx.niche_disclaimer,
                        cta_line="",
                        narrative_mode=page_ctx.narrative_mode,
                        batch_angle_block=_batch_angle_block,
                        uniqueness_rejection=_seq_reject,
                        previously_generated_hooks=_hooks_for_seq,
                        asset_captions=_asset_captions or None,
                    ) or ""
                    if _early_seq_script:
                        _LOG.info(
                            "SEQUENCE_REEL | early script drafted (%d words) — "
                            "seed only, downstream measures actual TTS duration.",
                            len(_early_seq_script.split()),
                        )
                    if uniqueness_guard is None or not _early_seq_script:
                        break
                    _opening = " ".join((_early_seq_script or "").split()[:80])
                    _ok_s, _prior_s, _score_s = uniqueness_guard.try_claim(_opening)
                    if _ok_s:
                        _LOG.info(
                            "SEQUENCE uniqueness OK | variant=%d attempt=%d",
                            variant + 1, _seq_attempt,
                        )
                        break
                    _seq_reject = uniqueness_guard.rejection_instruction(
                        _prior_s or "", _score_s
                    )
                    _LOG.warning(
                        "SEQUENCE uniqueness REJECT | variant=%d attempt=%d sim=%.0f%%",
                        variant + 1, _seq_attempt, _score_s * 100,
                    )
                    if _seq_attempt >= MAX_UNIQUENESS_RETRIES:
                        uniqueness_guard.register(_opening)
                        break
                if _early_seq_script:
                    if page_ctx and (page_ctx.page_id or "").lower() == "ancient_knowledge":
                        from modules.reel_artefact.script_length import (
                            AK_NARRATION_S,
                            soft_ceiling,
                        )

                        _early_seq_script = soft_ceiling(
                            _early_seq_script, max_seconds=AK_NARRATION_S,
                        )
                    else:
                        _early_seq_script = _trim_script_to_word_limit(
                            _early_seq_script,
                            max_words=_seq_words + 15,
                        )
                    if cost_tracker is not None:
                        cost_tracker.track_text(char_count=len(_early_seq_script))
                    _LOG.info(
                        "SEQUENCE_REEL | early script ready for act-image sync (%d words, %d acts)",
                        len(_early_seq_script.split()), _seq_n,
                    )
            except Exception as _early_exc:  # noqa: BLE001
                if page_ctx and (page_ctx.page_id or "").lower() == "ancient_knowledge":
                    raise
                _LOG.warning(
                    "SEQUENCE_REEL early script failed (%s) — falling back to topic.",
                    _early_exc,
                )
                _early_seq_script = ""

        _ak_page = bool(
            page_ctx and (page_ctx.page_id or "").lower() == "ancient_knowledge"
        )
        if _ak_page:
            from modules.reel_artefact.script_grounding import (
                grounding_problem as _grounding_problem,
            )
            from modules.reel_artefact.script_grounding import (
                script_is_usable as _script_usable,
            )

            _topic_text = str(resolved_subject or "")
            if _early_seq_script and _grounding_problem(
                _early_seq_script, _topic_text, channel="ancient_knowledge",
            ):
                _ground_why = _grounding_problem(
                    _early_seq_script, _topic_text, channel="ancient_knowledge",
                )
                _retry_note = _ground_why
                if _ground_why == "hook does not name the topic":
                    _retry_note = (
                        f'The first sentence must contain "{_topic_text}".'
                    )
                elif _ground_why.startswith("camera"):
                    _retry_note = (
                        "Rewrite without camera directions. Do not mention fog "
                        "rolling, golden hour, aerial views, foliage, rock "
                        "formations, or water flowing as a shot."
                    )
                elif _ground_why.startswith("tone"):
                    _retry_note = (
                        "Rewrite in a National Geographic tone. Use "
                        "'some researchers suggest'. Do not say archaeology "
                        "refuses, and do not say a building powered devices."
                    )
                _LOG.warning("SCRIPT GROUNDING | %s | one retry", _ground_why)
                if caption_engine is not None:
                    _early_seq_script = caption_engine.generate_sequence_voiceover(
                        resolved_subject,
                        page_niche=page_ctx.content_niche,
                        persona_voice=page_ctx.tts_voice_preference or (
                            "investigative, neutral, immersive"
                        ),
                        n_acts=_seq_n,
                        duration_s=page_ctx.reel_duration,
                        total_words_target=_seq_words,
                        economic=economic,
                        niche_disclaimer=page_ctx.niche_disclaimer,
                        cta_line="",
                        narrative_mode=page_ctx.narrative_mode,
                        batch_angle_block=_batch_angle_block,
                        uniqueness_rejection=_retry_note,
                        previously_generated_hooks=list(generated_hooks_cache or []),
                        asset_captions=_asset_captions or None,
                    ) or ""
                    if _early_seq_script:
                        from modules.reel_artefact.script_length import (
                            AK_NARRATION_S,
                            soft_ceiling,
                        )

                        _early_seq_script = soft_ceiling(
                            _early_seq_script, max_seconds=AK_NARRATION_S,
                        )
            _ground_left = _grounding_problem(
                _early_seq_script or "", _topic_text, channel="ancient_knowledge",
            ) if _early_seq_script else "empty script"
            if (
                _ground_left == "hook does not name the topic"
                and _script_usable(_early_seq_script or "", _topic_text)
            ):
                from modules.reel_artefact.script_grounding import (
                    repair_hook as _repair_hook,
                )

                _early_seq_script = _repair_hook(_early_seq_script, _topic_text)
                _ground_left = _grounding_problem(
                    _early_seq_script, _topic_text, channel="ancient_knowledge",
                )
                _LOG.info(
                    "SCRIPT GROUNDING | hook repaired | %s",
                    _topic_text[:80],
                )
            if not _script_usable(_early_seq_script or "", _topic_text) or _ground_left:
                _saved_script = ""
                try:
                    from modules.reel_artefact.checkpoint import (
                        ReelCheckpoint as _ScriptCkpt,
                    )

                    _saved_script = _ScriptCkpt.for_reel(
                        "ancient_knowledge", stem,
                    ).load_script()
                except Exception:  # noqa: BLE001
                    _saved_script = ""
                _saved_ok = _script_usable(_saved_script, _topic_text) and not (
                    _grounding_problem(
                        _saved_script, _topic_text, channel="ancient_knowledge",
                    )
                )
                if _saved_ok:
                    _early_seq_script = _saved_script
                    _LOG.info(
                        "SCRIPT resume | words=%d", len(_saved_script.split()),
                    )
                else:
                    raise RuntimeError(
                        "narration script empty or ungrounded after retries — "
                        "refusing to plan images from the topic"
                        + (f" ({_ground_left})" if _ground_left else "")
                    )
            else:
                try:
                    from modules.reel_artefact.provider_health import text_writer_banner

                    _writer = text_writer_banner()
                    if _writer:
                        bm.humanizer_summary = _writer
                        humanizer_notes = _writer
                        _LOG.info("Models banner | humanizer=%s", _writer)
                except Exception:  # noqa: BLE001
                    pass
                try:
                    from modules.reel_artefact.checkpoint import (
                        ReelCheckpoint as _ScriptCkpt,
                    )

                    _ScriptCkpt.for_reel("ancient_knowledge", stem).save_script(
                        _early_seq_script,
                    )
                except Exception:  # noqa: BLE001
                    pass
        _beat_source = _early_seq_script if _ak_page else (
            _early_seq_script
            or (caption if caption and caption != "(skipped)" else resolved_subject)
        )
        _spoken_snippets = _segment_script_into_act_snippets(
            _beat_source,
            _seq_n,
        )

        _audio_first_scenes: list[dict] = []
        # Audio-first: TTS + timestamps before ANY act stills (all sequence pages).
        if post_type in ("ECONOMIC_REEL", "WAN_REEL"):
            _clips_dir = Path(app_config.PAGE_OUTPUTS_DIR) / "clips"
            _clips_dir.mkdir(parents=True, exist_ok=True)
            (
                _voice_path,
                _narr_word_timings,
                _narr_dur,
                _ak_total_audio,
                _early_seq_script,
                _cta_audio_path,
                _cta_audio_dur,
                _cta_silence_s,
            ) = _synthesize_sequence_voice_track(
                _beat_source,
                reel_dir=_clips_dir,
                stem=stem,
                variant=variant,
                page_ctx=page_ctx,
                caption_engine=caption_engine,
                resolved_subject=resolved_subject,
                economic=economic,
                n_acts=_seq_n,
                batch_angle_block=_batch_angle_block,
                generated_hooks_cache=generated_hooks_cache,
                cost_tracker=cost_tracker,
            )
            _commit_reel_angle(
                caption_engine, page_ctx, resolved_subject, _early_seq_script,
            )
            if _voice_path is not None and _narr_dur > 0.5:
                _ak_voice_ready = True
                if cost_tracker is not None:
                    _early_tts_chars = len(_early_seq_script or "")
                    _early_cta = (page_ctx.reel_cta_text if page_ctx else "") or ""
                    _early_tts_chars += len(_early_cta)
                    if _early_tts_chars > 0:
                        from modules.reel_artefact.checkpoint import tts_was_resumed as _tts_resumed

                        if not _tts_resumed():
                            cost_tracker.track_audio(char_count=_early_tts_chars, sfx=False)
                # Round-4 fix: hook/body-bucket/CTA planner runs AFTER measured
                # audio (narration + silence + CTA) is known. Never estimates.
                from core.reel_sequence_engine import (
                    plan_bucket_act_durations as _plan_bucket_act_durations,
                )
                # Per-act snippets from the FINAL script's own act boundaries —
                # no timing/no Gemini needed. Guarantees Hook/Body word counts
                # come from the actual spoken content.
                _pre_snippets = _segment_script_into_act_snippets(
                    _early_seq_script or resolved_subject,
                    _seq_n,
                )
                # Fill any empty snippet with topic so bucketing (word count)
                # never sees len(0) collapsing an act into a merge target.
                _pre_snippets = [s or resolved_subject for s in _pre_snippets]
                if post_type == "WAN_REEL":
                    # Keep ~7 s clips in sync with measured audio. Do not run
                    # the ECONOMIC_REEL bucket planner (it created 3–5.5 s holds).
                    _audio_total = float(
                        locals().get("_ak_total_audio") or 0.0
                    ) or (
                        float(_narr_dur)
                        + float(_cta_audio_dur or 0.0)
                        + float(_cta_silence_s or 1.0)
                    )
                    _wan_n = max(8, min(14, int(round(_audio_total / 7.0)))) if _audio_total > 0.5 else _seq_n
                    if _wan_n != _seq_n:
                        _LOG.info(
                            "WAN_REEL act recount after TTS | audio=%.1fs %d→%d acts (~7s)",
                            _audio_total, _seq_n, _wan_n,
                        )
                        _seq_n = _wan_n
                        _pre_snippets = _segment_script_into_act_snippets(
                            _early_seq_script or resolved_subject,
                            _seq_n,
                        )
                        _pre_snippets = [s or resolved_subject for s in _pre_snippets]
                    _unit = (_audio_total / _seq_n) if _seq_n else 7.0
                    _ak_act_durs = [_unit] * _seq_n
                    _spoken_snippets = list(_pre_snippets)
                    _ak_keep_map = list(range(_seq_n))
                    _LOG.info(
                        "WAN_REEL equal holds | n=%d hold=%.2fs sum=%.1fs (target ~7s)",
                        _seq_n, _unit, _unit * _seq_n,
                    )
                else:
                    # One beat per narration sentence, timed by the word timestamps.
                    if (not _is_mm) and _narr_word_timings:
                        try:
                            from modules.reel_qa.beats import (
                                _scene_for as _sentence_scene,
                                beats_from_timestamps,
                            )

                            _cta_len = float(_cta_audio_dur or 0.0)
                            _silence = (
                                float(_cta_silence_s or 0.0) if _cta_len > 0.05 else 0.0
                            )
                            _timeline_audio = float(
                                locals().get("_ak_total_audio") or 0.0
                            )
                            if _timeline_audio <= 0.5:
                                _timeline_audio = (
                                    float(_narr_dur or 0.0) + _silence + _cta_len
                                )
                            _tile_end = (
                                float(_narr_dur or 0.0) + _silence
                                if _cta_len > 0.05
                                else _timeline_audio
                            )
                            _rows = beats_from_timestamps(
                                _early_seq_script or resolved_subject,
                                _narr_word_timings,
                                narration_s=float(_narr_dur or 0.0),
                                audio_end=_tile_end,
                            )
                            if _rows:
                                _audio_first_scenes = []
                                _ak_act_durs = []
                                _sentence_starts = []
                                for _i, (_text, _start, _end) in enumerate(_rows):
                                    _dur = max(0.05, float(_end) - float(_start))
                                    _ak_act_durs.append(_dur)
                                    _sentence_starts.append(float(_start))
                                    _audio_first_scenes.append(_sentence_scene(
                                        _text,
                                        float(_start),
                                        float(_start) + _dur,
                                        index=_i,
                                        inherited={"domain_anchors": resolved_subject},
                                    ))
                                _spoken_snippets = [
                                    str(s.get("spoken_text") or resolved_subject)
                                    for s in _audio_first_scenes
                                ]
                                _seq_n = len(_audio_first_scenes)
                                _ak_keep_map = list(range(_seq_n))
                                _LOG.info(
                                    "SENTENCE BEATS | n=%d durs=%s",
                                    _seq_n,
                                    ",".join(f"{d:.2f}" for d in _ak_act_durs[:8]),
                                )
                                for _sc in _audio_first_scenes:
                                    _LOG.info(
                                        "SENTENCE BEAT | %s [%.1f–%.1f] %s",
                                        _sc.get("scene_index"),
                                        float(_sc.get("start_time") or 0),
                                        float(_sc.get("end_time") or 0),
                                        str(_sc.get("spoken_text") or "")[:80],
                                    )
                        except Exception as _chunk_exc:  # noqa: BLE001
                            _LOG.warning(
                                "SENTENCE BEATS failed (%s) — bucket planner",
                                _chunk_exc,
                            )
                            _audio_first_scenes = []
                            _sentence_starts = []
                    if not _audio_first_scenes:
                        _bucket_kwargs: dict = {}
                        _t1_body_local = locals().get("_tier1_body_target", None)
                        if (
                            getattr(page_ctx, "use_two_tier_pacing", False)
                            and _t1_body_local is not None
                            and int(_t1_body_local) > 0
                        ):
                            _bucket_kwargs["tier2_body_start"] = int(_t1_body_local)
                        _ak_act_durs, _spoken_snippets, _ak_keep_map = (
                            _plan_bucket_act_durations(
                                _pre_snippets,
                                narration_s=float(_narr_dur),
                                cta_audio_s=float(_cta_audio_dur or 0.0),
                                silence_before_cta_s=float(_cta_silence_s or 1.0),
                                **_bucket_kwargs,
                            )
                        )
                _ak_bucket_snippets = list(_spoken_snippets)
                _spoken_snippets = [
                    s or resolved_subject for s in _spoken_snippets
                ]
                if len(_ak_act_durs) != _seq_n:
                    _LOG.info(
                        "Bucket planner | merged %d short act(s) → keeping %d/%d stills",
                        _seq_n - len(_ak_act_durs), len(_ak_act_durs), _seq_n,
                    )
                    _seq_n = len(_ak_act_durs)
                _LOG.info(
                    "BUCKET PLAN | n=%d durs=%s beats=%s",
                    _seq_n,
                    ",".join(f"{d:.2f}" for d in _ak_act_durs),
                    " | ".join(
                        f"{i+1}[{len((_ak_bucket_snippets[i] or '').split())}w→{_ak_act_durs[i]:.1f}s]:"
                        f"{(_ak_bucket_snippets[i] or '')[:40]}"
                        for i in range(_seq_n)
                    ),
                )

        if _is_sequence_video_post(post_type):
            from modules.reel_qa.beats import refine_reel_beats

            _beat_plan = refine_reel_beats(
                snippets=list(_spoken_snippets or []),
                durations=list(_ak_act_durs or []),
                word_timings=list(_narr_word_timings or []),
                narration_s=float(_narr_dur or 0.0),
                scenes=list(_audio_first_scenes or []),
                topic=str(resolved_subject or ""),
            )
            if _beat_plan.changed:
                _spoken_snippets = list(_beat_plan.snippets)
                _ak_act_durs = list(_beat_plan.durations)
                _seq_n = len(_spoken_snippets)
                _audio_first_scenes = list(_beat_plan.scenes)
                _LOG.info(
                    "REEL BEATS | n=%d max_s=%.1f durs=%s",
                    _seq_n,
                    max(_beat_plan.durations or [0.0]),
                    ",".join(f"{d:.2f}" for d in (_beat_plan.durations or [])[:8]),
                )
        _cta_beat_index = None
        if (
            page_ctx
            and (page_ctx.page_id or "").lower() == "ancient_knowledge"
            and _spoken_snippets
            and _ak_act_durs
        ):
            _cta_line = (page_ctx.reel_cta_text or "").strip()
            if _sentence_starts and float(_timeline_audio or 0.0) > 0.5:
                from modules.reel_qa.beats import seal_sentence_timeline as _seal_tl

                _spoken_snippets, _ak_act_durs = _seal_tl(
                    list(_spoken_snippets),
                    list(_ak_act_durs),
                    list(_sentence_starts),
                    float(_timeline_audio),
                    cta_text=_cta_line,
                )
                _seq_n = len(_spoken_snippets)
                _LOG.info(
                    "TIMELINE | beats=%d video=%.2fs audio=%.2fs",
                    len(_ak_act_durs),
                    sum(_ak_act_durs),
                    float(_timeline_audio),
                )
            try:
                from modules.reel_qa.cta_beat import with_cta_beat as _with_cta

                _cta_hold = float(locals().get("_cta_audio_dur") or 0.0) + float(
                    locals().get("_cta_silence_s") or 1.0
                )
                if _cta_line:
                    _spoken_snippets, _ak_act_durs, _cta_beat_index = _with_cta(
                        list(_spoken_snippets),
                        list(_ak_act_durs),
                        _cta_line,
                        _cta_hold,
                    )
                    _seq_n = len(_spoken_snippets)
                    _LOG.info(
                        "CTA BEAT | index=%s hold=%.2fs line=%.60s",
                        _cta_beat_index,
                        _cta_hold,
                        _cta_line,
                    )
            except Exception as _cta_beat_exc:  # noqa: BLE001
                _LOG.warning("CTA beat skipped (%s)", _cta_beat_exc)

        if _asset_pool and _spoken_snippets:
            try:
                from modules.reel_artefact.asset_pool import fit_pool_beats as _fit_beats

                _protect_cta = (
                    1
                    if _cta_beat_index is not None
                    and int(_cta_beat_index) == len(_spoken_snippets) - 1
                    else 0
                )
                _fit_snips, _fit_durs, _asset_assignments = _fit_beats(
                    list(_spoken_snippets),
                    list(_ak_act_durs or []),
                    list(_asset_pool),
                    geography=_pool_geography,
                    flux_max=6,
                    protect_tail=_protect_cta,
                )
                _durs_match = bool(_ak_act_durs) and abs(
                    sum(_fit_durs) - sum(_ak_act_durs)
                ) < 0.05
                if (
                    _fit_snips
                    and _fit_durs
                    and len(_fit_snips) == len(_fit_durs) == len(_asset_assignments)
                    and _durs_match
                ):
                    _spoken_snippets = list(_fit_snips)
                    _ak_act_durs = list(_fit_durs)
                    _seq_n = len(_spoken_snippets)
                    if _protect_cta:
                        _cta_beat_index = len(_spoken_snippets) - 1
                elif len(_asset_assignments) != len(_spoken_snippets):
                    _LOG.warning(
                        "ASSET SHOTS length mismatch | shots=%d beats=%d",
                        len(_asset_assignments),
                        len(_spoken_snippets),
                    )
                    _asset_assignments = []
                _LOG.info(
                    "ASSET SHOTS | n=%d flux=%d",
                    len(_asset_assignments),
                    sum(1 for item in _asset_assignments if item.mode == "flux"),
                )
            except Exception as _assign_exc:  # noqa: BLE001
                _LOG.warning("ASSET SHOTS skipped (%s)", _assign_exc)
                _asset_assignments = []

        # Act 1: timestamp-bound generation for AK; cover reuse only as fallback.
        # Master Mei regenerates Scene 1 via FLUX (never graphite raw_bg).
        if not _is_mm and not _ak_voice_ready:
            _sequence_image_paths.append(
                _ensure_sequence_image(raw_bg_path, fallback=raw_bg_path)
            )
        _act1_base = raw_bg_path

        if _is_mm:
            try:
                from agents.media.visual_roles import (
                    pick_mei_meditation_environment as _mei_pick_env_prod,
                    select_style_references_for_mei_frame as _mei_select_srefs,
                )
                _hook_env = _mei_pick_env_prod(
                    episode_seed=resolved_subject or "",
                    spoken_beat=_early_seq_script or (
                        caption if caption and caption != "(skipped)" else ""
                    ),
                )
            except Exception:
                _hook_env = "mist-covered peak cliff above a sea of clouds"

                def _mei_select_srefs(refs, *, act_index, episode_seed=""):  # type: ignore[misc]
                    return [] if int(act_index) == 0 else list(refs or [])
            from agents.media.avatar_engine.mei_visual import (
                MASTER_STYLE_ANCHOR_DEFAULT as _MM_STYLE_DEFAULT_PROD,
            )
            from agents.media.style_reader import (
                default_master_mei_ref_folder as _mm_ref_folder_prod,
            )
            _mm_fixed_anchor = (
                page_ctx.master_style_anchor or _MM_STYLE_DEFAULT_PROD
            ).strip() or _MM_STYLE_DEFAULT_PROD
            _mm_prompts, _mm_slots, _mm_roles, _mm_negatives = (
                _build_master_mei_script_act_prompts(
                    subject=resolved_subject,
                    script=_early_seq_script or (
                        caption if caption and caption != "(skipped)" else resolved_subject
                    ),
                    n_acts=_seq_n,
                    base_style=_ak_base_style,
                    visual_dna=page_ctx.master_mei_visual_dna or _MEI_DNA_FALLBACK,
                    hook_env=_hook_env,
                    master_style_anchor=_mm_fixed_anchor,
                    reference_folder=_mm_ref_folder_prod(),
                    use_vision_style=False,  # GOLDEN LOCK — no Vision prompt rewrite
                )
            )
            _LOG.info(
                "MASTER_MEI 3-ROLE distribution | roles=%s | avatar_slots=%s",
                _mm_roles, sorted(_mm_slots),
            )
            # MODULE 3 — dynamic per-page style reference (channels_config/<page>/style_reference/)
            _mm_style_refs = page_ctx.resolve_style_reference_images()
            _mm_style_weight = page_ctx.style_reference_weight
            if _mm_style_refs:
                _LOG.info(
                    "MASTER_MEI | %d style reference image(s) loaded (weight=%.2f) → %s",
                    len(_mm_style_refs), _mm_style_weight,
                    ", ".join(p.name for p in _mm_style_refs),
                )
            # ZERO HALLUCINATION: always bind canonical avatar.png for Mei frames
            _mm_avatar_ref: "Path | None" = None
            _mm_avatar_w: float | None = None
            if not page_ctx.sequence_force_avatar_off:
                _mm_avatar_ref = _assert_mei_avatar(app_config.ENGINE_ROOT)
                if not _mm_avatar_ref.is_file():
                    # Fallback to channel_loader path if assert path missing
                    _forced_mm = page_ctx.forced_avatar_reference_path
                    if _forced_mm is not None and _forced_mm.is_file():
                        _mm_avatar_ref = _forced_mm
                    else:
                        _LOG.error(
                            "MASTER_MEI | avatar.png MISSING — Mei frames will fail "
                            "zero-hallucination lock. Expected: %s",
                            _resolve_mei_avatar_path(app_config.ENGINE_ROOT),
                        )
                        _mm_avatar_ref = None
                if _mm_avatar_ref is not None:
                    _mm_avatar_w = page_ctx.avatar_image_weight
                    _LOG.info(
                        "MASTER_MEI | avatar likeness LOCKED → %s (weight=%.2f)",
                        _mm_avatar_ref, _mm_avatar_w or 0.0,
                    )
            # PARALLEL IMAGE GENERATION — build all act jobs (cheap, sequential),
            # then fire them concurrently (ThreadPoolExecutor max_workers=5). The
            # Together AI adapter's global rate limiter still paces the actual
            # HTTP calls, so this only removes orchestration/IO wait time.
            _mm_act_meta: dict[int, dict[str, Any]] = {}
            _mm_act_jobs: dict[int, Any] = {}
            # Include Act 1 (index 0) — identity lock + unique Frame 1 scene; never graphite base
            for _act_i in range(0, _seq_n):
                _snippet = (
                    _spoken_snippets[_act_i]
                    if _act_i < len(_spoken_snippets)
                    else resolved_subject
                )
                _act_prompt = _mm_prompts[_act_i] if _act_i < len(_mm_prompts) else (
                    "Cinematic photorealistic dystopian visual scene, no text."
                )
                # Hard strip any legacy graphite / drawing pollution before FLUX
                import re as _re_flux_purge
                _act_prompt = _re_flux_purge.sub(
                    r"(?i)\b(?:a\s+precise\s+)?graphite\s+(?:drawing|scene|sketch|illustration)"
                    r"(?:\s+of\s+a\s+man)?\b[^.]*\.?",
                    " ",
                    _act_prompt,
                )
                _act_prompt = _re_flux_purge.sub(
                    r"(?i)\bOriginal\s+scene\s+concept\s*:\s*",
                    "",
                    _act_prompt,
                )
                _act_prompt = _re_flux_purge.sub(r"\s{2,}", " ", _act_prompt).strip(" ,.")
                _act_role = (
                    _mm_roles[_act_i] if _act_i < len(_mm_roles) else "slave"
                )
                _act_neg = (
                    _mm_negatives[_act_i] if _act_i < len(_mm_negatives) else ""
                )
                _act_theme = _classify_mei_visual_theme(
                    f"{resolved_subject} {_snippet}"
                )
                # CONDITIONAL AVATAR: Role A (Master) only — never disciples/slaves
                _use_avatar = (
                    _mm_avatar_ref is not None
                    and _act_i in _mm_slots
                    and _act_role == "master"
                )
                _fallback_dest = _reel_img_dir / f"{stem}_act{_act_i + 1:02d}_fallback.png"
                _mm_act_meta[_act_i] = {
                    "snippet": _snippet,
                    "theme": _act_theme,
                    "role": _act_role,
                    "use_avatar": _use_avatar,
                    "fallback_dest": _fallback_dest,
                }
                _mm_act_jobs[_act_i] = functools.partial(
                    adapter.generate,
                    _act_prompt,
                    output_stem=f"{stem}_act{_act_i + 1:02d}",
                    output_directory=_reel_img_dir,
                    reference_image_path=_mm_avatar_ref if _use_avatar else None,
                    avatar_mode="ON" if _use_avatar else "OFF",
                    reference_image_weight=_mm_avatar_w if _use_avatar else None,
                    # Style refs OFF for Scene 3 / penultimate — avoid graphite ref bleed
                    style_reference_paths=(
                        _mei_select_srefs(
                            _mm_style_refs,
                            act_index=_act_i,
                            episode_seed=resolved_subject or "",
                            n_acts=_seq_n,
                        )
                        if _use_avatar and _act_i not in (2, _seq_n - 2)
                        else None
                    ) or None,
                    style_reference_weight=(
                        _mm_style_weight
                        if _use_avatar and _act_i not in (2, _seq_n - 2)
                        else None
                    ),
                    visual_role=_act_role,
                    negative_prompt=_act_neg or None,
                )

            _mm_gpu_baseline = 0.0
            try:
                from core.remote_gpu_manager import (  # noqa: PLC0415
                    get_manager as _get_rgpu_mm,
                    is_remote_gpu_enabled as _rgpu_on_mm,
                )

                if _rgpu_on_mm("image"):
                    _mm_gpu_baseline = float(
                        getattr(_get_rgpu_mm().client, "total_gpu_seconds", 0) or 0
                    )
            except Exception:
                _mm_gpu_baseline = 0.0

            _apply_still_cache_to_jobs(
                jobs=_mm_act_jobs,
                meta=_mm_act_meta,
                n_acts=_seq_n,
                start=0,
                work_dir=_reel_img_dir,
                stem=stem,
                extra_dirs=[subject_assets] if subject_assets else None,
            )

            _mm_act_results = _run_acts_parallel(_mm_act_jobs)

            _mm_ok = 0
            for _act_i in range(0, _seq_n):
                _meta = _mm_act_meta[_act_i]
                _snippet = _meta["snippet"]
                _act_theme = _meta["theme"]
                _use_avatar = _meta["use_avatar"]
                _fallback_dest = _meta["fallback_dest"]
                _cached_still = _meta.get("cached_path")
                if _cached_still is not None:
                    _act_img = _ensure_sequence_image(
                        _cached_still, fallback=_cached_still, target_path=_fallback_dest,
                    )
                    _sequence_image_paths.append(_act_img)
                    if _act_i == 0:
                        _act1_base = _act_img
                    _LOG.info(
                        "MASTER_MEI sequence | act %d/%d CACHED | avatar=%s theme=%s | beat=%.40s…",
                        _act_i + 1, _seq_n,
                        "ON" if _use_avatar else "OFF", _act_theme, _snippet,
                    )
                    continue
                _result = _mm_act_results.get(_act_i)
                if isinstance(_result, Exception):
                    _LOG.warning(
                        "MASTER_MEI act %d failed (%s) — blinded fallback.",
                        _act_i + 1, _result,
                    )
                    _fb = _act1_base if _act_i > 0 and _sequence_image_paths else raw_bg_path
                    _sequence_image_paths.append(
                        _ensure_sequence_image(
                            _fallback_dest, fallback=_fb, target_path=_fallback_dest,
                        )
                    )
                    continue
                _fb = (
                    _sequence_image_paths[0]
                    if _sequence_image_paths
                    else raw_bg_path
                )
                _act_img = _ensure_sequence_image(
                    _result, fallback=_fb, target_path=_fallback_dest,
                )
                _sequence_image_paths.append(_act_img)
                _mm_ok += 1
                if _act_i == 0:
                    _act1_base = _act_img
                _LOG.info(
                    "MASTER_MEI sequence | act %d/%d | avatar=%s theme=%s | beat=%.40s…",
                    _act_i + 1, _seq_n,
                    "ON" if _use_avatar else "OFF", _act_theme, _snippet,
                )
            _mm_gpu_delta = _track_remote_gpu_batch(
                cost_tracker, seconds_before=_mm_gpu_baseline, jobs=_mm_ok,
            )
            if _mm_ok:
                if cost_tracker is not None:
                    cost_tracker.track_image(model_key="image_flux_schnell", count=_mm_ok)
                _images_generated_this_variant += _mm_ok
        else:
            # ancient_knowledge (+ other sequence pages): spoken-snippet → visual
            _act_descriptors = _build_reel_act_descriptors(
                resolved_subject, _seq_n, page_id=page_ctx.page_id or ""
            )
            _topic_entity_ctx = _extract_topic_visual_entities(resolved_subject)
            # TOPIC ANCHOR grounds identity (place, material, era) only — it must
            # never dictate camera framing or lighting, because those are owned
            # per-act by the shot / lighting pool. Historic "VISUAL SUBJECT:"
            # label was replaced 2026-08-15 because FLUX weighted it above the
            # per-act SUBJECT-pool directive.
            _topic_entity_prefix = (
                f"TOPIC ANCHOR: {_topic_entity_ctx}. " if _topic_entity_ctx else ""
            )
            from agents.media.prompt_alignment import (
                build_aligned_visual_block,
                plan_episode_visual_sequence,
            )
            # Per-episode 3-dimensional visual plan — one (subject-type,
            # shot-type, lighting) tuple per act, shuffled by the episode stem
            # with no two consecutive acts repeating on ANY of the three
            # dimensions, and doorway/portal/corridor subjects capped to at
            # most 1 per episode (channel RAG's forbidden_tokens list is
            # merged into the cap). The subject-pool item drives the scene
            # concept itself — shot + lighting are then layered on top so
            # every FLUX call gets an unambiguous per-act "SUBJECT + SHOT +
            # LIGHTING" directive instead of the old centred-doorway default.
            _ak_visual_plan = plan_episode_visual_sequence(
                _seq_n,
                topic=resolved_subject,
                seed=str(stem or resolved_subject or ""),
                channel_name=(page_ctx.page_id or "") if page_ctx else "",
            )
            # Per-episode channel-RAG image-guidance block (mandatory
            # elements, lighting_style, forbidden_tokens, visual_concepts).
            # Injected into every act's prompt so FLUX also sees the RAG's
            # rules, not just the pool-driven subject/shot/lighting.
            try:
                from agents.rag.channel_rag import get_image_guidance  # noqa: PLC0415

                _ak_rag_image_block = get_image_guidance(
                    (page_ctx.page_id or "") if page_ctx else ""
                )
            except Exception:  # noqa: BLE001
                _ak_rag_image_block = ""
            _parallax_directive = (
                "DEPTH LAYERS: Compose the frame with distinct foreground, "
                "mid-ground and background planes so camera motion creates true "
                "parallax separation. Do NOT default to a centered symmetric "
                "monument-frame composition unless the spoken beat explicitly "
                "requires it — vary spatial arrangement per act. "
            )
            # Lighting bias stripped — the per-act LIGHTING directive from the
            # shot+lighting plan now owns colour temperature / direction. Keep
            # only technical/format bits here so consecutive images don't all
            # collapse to "volumetric shafts + warm amber rim light".
            _lighting_tail = (
                "TECHNICAL: Ultra-realistic photography, 35mm film grain, "
                "full-bleed, no borders, no frames, no captions, no watermarks."
            )
            # PARALLEL IMAGE GENERATION — build all act jobs (cheap, sequential),
            # then fire them concurrently (ThreadPoolExecutor max_workers=5); the
            # Together AI adapter's global rate limiter still paces the actual
            # HTTP calls under the hood.
            _sr_act_meta: dict[int, dict[str, Any]] = {}
            _sr_act_jobs: dict[int, Any] = {}
            _sr_act_start = 0 if _ak_voice_ready else 1
            _planned_shots = None
            try:
                from modules.shot_planner.plan import plan_for_reel

                _planned_shots = plan_for_reel(
                    channel=str((page_ctx.page_id if page_ctx else "") or ""),
                    topic=str(resolved_subject or ""),
                    script=str(_early_seq_script or ""),
                    tracker=cost_tracker,
                )
            except Exception as _plan_exc:  # noqa: BLE001
                _LOG.warning("[ShotPlanner] fell back to resolver: %s", _plan_exc)
                _planned_shots = None
            _reel_checkpoint = None
            _ladder_on = False
            try:
                from modules.reel_artefact.budget import beat_budget_usd as _beat_usd
                from modules.reel_artefact.budget import nano_cap as _nano_cap
                from modules.reel_artefact.budget import reel_budget_usd as _reel_usd
                from modules.reel_artefact.checkpoint import ReelCheckpoint as _ReelCkpt
                from modules.reel_artefact.ladder import rung_names as _rung_names
                from modules.reel_artefact.policy import artefact_images_enabled as _ladder_flag

                # The ladder is an Ancient Knowledge feature. Master Mei and
                # other sequence pages keep their Flux path even when the
                # container sets REEL_ARTIFACT_IMAGES=1 for AK.
                _ladder_pages = {
                    part.strip().lower()
                    for part in (
                        os.getenv("REEL_ARTIFACT_IMAGES_CHANNELS") or "ancient_knowledge"
                    ).split(",")
                    if part.strip()
                }
                _ladder_on = bool(_ladder_flag()) and (
                    str((page_ctx.page_id if page_ctx else "") or "").strip().lower()
                    in _ladder_pages
                )
                if _ladder_on:
                    _reel_checkpoint = _ReelCkpt.for_reel(
                        str((page_ctx.page_id if page_ctx else "") or "ancient_knowledge"),
                        stem,
                    )
                    _LOG.info(
                        "LADDER | checkpoint=%s | rungs=%s | beat_usd=%.2f | "
                        "reel_usd=%.2f | nano_cap=%s",
                        _reel_checkpoint.path,
                        ",".join(_rung_names()),
                        _beat_usd(),
                        _reel_usd(),
                        _nano_cap(),
                    )
            except Exception as _ladder_exc:  # noqa: BLE001
                _LOG.warning("LADDER setup skipped (%s)", _ladder_exc)
                _ladder_on = False
                _reel_checkpoint = None
            from modules.reel_grounding.config import (
                grounding_enabled_for as _grounding_for_channel,
            )

            for _act_i in range(_sr_act_start, _seq_n):
                _snippet = (
                    _spoken_snippets[_act_i]
                    if _act_i < len(_spoken_snippets)
                    else resolved_subject
                )
                _prev_snip = (
                    _spoken_snippets[_act_i - 1]
                    if _act_i > 0 and (_act_i - 1) < len(_spoken_snippets)
                    else ""
                )
                _next_snip = (
                    _spoken_snippets[_act_i + 1]
                    if (_act_i + 1) < len(_spoken_snippets)
                    else ""
                )
                # Pull this act's (subject, shot, lighting) from the plan;
                # subject-pool text now drives the scene concept itself, so
                # the legacy _act_descriptors[_act_i] is only kept as a
                # last-resort fallback if the plan is somehow short.
                _plan_entry = (
                    _ak_visual_plan[_act_i]
                    if _act_i < len(_ak_visual_plan)
                    else None
                )
                if _plan_entry:
                    _subject_pair = _plan_entry["subject"]
                    _shot_pair = _plan_entry["shot"]
                    _light_pair = _plan_entry["lighting"]
                    _act_desc = _subject_pair[1]
                else:
                    _subject_pair = ("(fallback-arc)", "")
                    _shot_pair = None
                    _light_pair = None
                    _act_desc = (
                        _act_descriptors[_act_i]
                        if _act_i < len(_act_descriptors)
                        else f"ACT {_act_i + 1}: {resolved_subject}."
                    )
                _align = build_aligned_visual_block(
                    spoken_snippet=_snippet,
                    act_index=_act_i,
                    total_acts=_seq_n,
                    main_subject=resolved_subject,
                    prev_snippet=_prev_snip,
                    shot_override=_shot_pair,
                    lighting_override=_light_pair,
                )
                _act_neg = None
                _scene_row = (
                    _audio_first_scenes[_act_i]
                    if _act_i < len(_audio_first_scenes)
                    else None
                )
                if _scene_row:
                    _snippet = str(_scene_row.get("spoken_text") or _snippet)
                    _act_neg = str(_scene_row.get("negative_prompt") or "") or None
                # Per-episode subject-type directive leads the prompt so FLUX
                # weights the pool item's "what to draw" instruction ABOVE the
                # topic prefix (which historically pushed every image toward a
                # centered pillar/doorway composition). Order: SUBJECT-TYPE
                # (drives concept) -> topic context -> base style -> align/
                # shot/lighting -> parallax + technical suffix -> RAG image
                # guidance (mandatory elements + forbidden tokens).
                _rag_tail = (
                    f"\n\n{_ak_rag_image_block}"
                    if _ak_rag_image_block
                    else ""
                )
                _act_banned = list((_scene_row or {}).get("banned_subjects") or [])
                _act_anchors = str((_scene_row or {}).get("domain_anchors") or "")
                if _scene_row and _scene_row.get("image_generation_prompt"):
                    # Spoken-window prompt is the source of truth (anti-slop).
                    # Strip inherited AK style landmarks (e.g. "Pyramid") that
                    # are not named in this spoken window — that list was the
                    # first-pass slop injector on Antikythera.
                    from agents.media.scene_prompt_generator import (
                        sanitize_channel_style as _sanitize_style,
                    )

                    _safe_style = _sanitize_style(
                        _ak_base_style, _snippet, _act_banned,
                    )
                    _act_prompt = (
                        f"{_scene_row['image_generation_prompt']} "
                        f"{_safe_style}. "
                        f"{_parallax_directive}"
                        f"{_lighting_tail}"
                    )
                else:
                    _act_prompt = (
                        f"{_act_desc} "
                        f"{_topic_entity_prefix}"
                        f"{_ak_base_style}. "
                        f"{_align} "
                        f"{_parallax_directive}"
                        f"{_lighting_tail}"
                        f"{_rag_tail}"
                    )
                _plan_shot = None
                if _planned_shots is not None:
                    from modules.shot_planner.plan import shot_for_beat
                    from modules.shot_planner.render import render_shot

                    _plan_shot = shot_for_beat(_planned_shots, str(_snippet or ""))
                if _plan_shot is not None:
                    _rendered = render_shot(
                        _plan_shot,
                        channel=str((page_ctx.page_id if page_ctx else "") or ""),
                    )
                    _act_prompt = _rendered.positive
                    if _rendered.negative:
                        _act_neg = _rendered.negative
                    resolved_subject_for_beat = _rendered.subject
                elif not _grounding_for_channel(
                    str((page_ctx.page_id if page_ctx else "") or "")
                ):
                    # Master Mei and other sequence pages keep their own prompt.
                    resolved_subject_for_beat = str(resolved_subject or "")
                else:
                    try:
                        from modules.reel_grounding.prompt import assemble_beat

                        _shot_name = _shot_pair[0] if _shot_pair else ""
                        _shot_text = _shot_pair[1] if _shot_pair else ""
                        _light_name = _light_pair[0] if _light_pair else ""
                        _light_text = _light_pair[1] if _light_pair else ""
                        _beat_plan = assemble_beat(
                            topic=str(resolved_subject or ""),
                            script=str(_early_seq_script or ""),
                            spoken=str(_snippet or ""),
                            draft=_act_prompt,
                            shot_name=str(_shot_name or ""),
                            shot_text=str(_shot_text or ""),
                            lighting_name=str(_light_name or ""),
                            lighting_text=str(_light_text or ""),
                            channel=str((page_ctx.page_id if page_ctx else "") or ""),
                            domain_anchors=str(_act_anchors or ""),
                            previous_spoken=str(_prev_snip or ""),
                            next_spoken=str(_next_snip or ""),
                            banned=list(_act_banned or []),
                            negative=str(_act_neg or ""),
                        )
                        _act_prompt = _beat_plan.positive
                        _act_neg = _beat_plan.negative or _act_neg
                        _act_banned = list(_beat_plan.banned)
                        if _beat_plan.object_label:
                            resolved_subject_for_beat = _beat_plan.object_label
                        else:
                            resolved_subject_for_beat = str(resolved_subject or "")
                    except Exception as _ground_exc:  # noqa: BLE001
                        _LOG.warning("visual grounding skipped (%s)", _ground_exc)
                        resolved_subject_for_beat = str(resolved_subject or "")
                if _cta_beat_index is not None and _act_i == _cta_beat_index:
                    from modules.reel_artefact.route import generic_flux_prompt as _cta_flux

                    _act_prompt = _cta_flux(
                        "Setting: a quiet museum hall or an ancient landscape. "
                        f"{_snippet}"
                    )
                    _plan_shot = None
                    resolved_subject_for_beat = str(_snippet or "closing line")
                # Log per-act plan for verifiability — this is the anti-monotony
                # signal that ends up in the compiled prompt.
                try:
                    _subj_name_log = _subject_pair[0] if _subject_pair else "(fallback)"
                    _shot_name_log = _shot_pair[0] if _shot_pair else "(fallback-cycle)"
                    _light_name_log = _light_pair[0] if _light_pair else "(none)"
                    _LOG.info(
                        "VISUAL PLAN | act %d/%d | via=%s | object=%s | "
                        "subject=%s | shot=%s | lighting=%s",
                        _act_i + 1, _seq_n,
                        "planner" if _plan_shot is not None else "resolver",
                        resolved_subject_for_beat,
                        _subj_name_log, _shot_name_log, _light_name_log,
                    )
                except Exception:
                    pass
                _fallback_dest = _reel_img_dir / f"{stem}_act{_act_i + 1:02d}_fallback.png"
                _act_stem = f"{stem}_act{_act_i + 1:02d}_r01"
                _sr_act_meta[_act_i] = {
                    "snippet": _snippet,
                    "fallback_dest": _fallback_dest,
                    "prompt": _act_prompt,
                    "stem": f"{stem}_act{_act_i + 1:02d}",
                    "banned_subjects": list(_act_banned or []),
                    "domain_anchors": str(_act_anchors or ""),
                    "negative_prompt": _act_neg,
                    "episode_subject": str(resolved_subject_for_beat or ""),
                }
                _gen = adapter.generate
                _art_kwargs: dict = {}
                if _plan_shot is not None:
                    try:
                        from modules.reel_artefact.hook import wrap_reel_generate

                        _gen, _art_kwargs = wrap_reel_generate(
                            adapter.generate,
                            channel=str((page_ctx.page_id if page_ctx else "") or ""),
                            shot=_plan_shot,
                        )
                    except Exception as _art_exc:  # noqa: BLE001
                        _LOG.warning("artefact still hook skipped (%s)", _art_exc)
                _sr_act_meta[_act_i]["generate_fn"] = _gen
                _sr_act_meta[_act_i]["artefact_kwargs"] = dict(_art_kwargs)
                _sr_act_meta[_act_i]["is_cta"] = bool(
                    _cta_beat_index is not None and _act_i == _cta_beat_index
                )
                from modules.reel_visual_qa import generate_and_gate as _qwen_gate

                _doc_role = (
                    "documentary"
                    if (page_ctx.page_id or "").lower() != "master_mei"
                    else None
                )
                if _asset_assignments and _act_i < len(_asset_assignments):
                    from modules.reel_artefact.asset_pool import make_pool_job as _make_pool_job

                    if _act_i == _sr_act_start:
                        _LOG.info(
                            "ASSET POOL | stills from the pool | ladder rounds skipped"
                        )
                    _sr_act_jobs[_act_i] = _make_pool_job(
                        _asset_assignments[_act_i],
                        _reel_img_dir / f"{stem}_act{_act_i + 1:02d}.jpg",
                        list(_asset_pool),
                        adapter.generate,
                        _pool_geography,
                        _act_i,
                    )
                elif _ladder_on:
                    from modules.reel_artefact.orchestrate import make_climb_job as _make_climb_job

                    _sr_act_jobs[_act_i] = _make_climb_job(
                        beat_index=_act_i,
                        narration=_snippet,
                        prompt=_act_prompt,
                        output_directory=_reel_img_dir,
                        stem=f"{stem}_act{_act_i + 1:02d}",
                        channel=str((page_ctx.page_id if page_ctx else "") or "ancient_knowledge"),
                        flux_generate=adapter.generate,
                        shot=_plan_shot,
                        checkpoint=_reel_checkpoint,
                        generate_kwargs={
                            "negative_prompt": _act_neg,
                            "visual_role": _doc_role,
                        },
                        episode_subject=str(
                            resolved_subject or resolved_subject_for_beat or ""
                        ),
                        banned_subjects=list(_act_banned or []) or None,
                        is_cta=bool(_sr_act_meta[_act_i]["is_cta"]),
                        n_beats=int(_seq_n),
                    )
                else:
                    _sr_act_jobs[_act_i] = functools.partial(
                    _qwen_gate,
                    _gen,
                    prompt=_act_prompt,
                    chunk_text=_snippet,
                    output_stem=_act_stem,
                    output_directory=_reel_img_dir,
                    channel=(page_ctx.page_id or "ancient_knowledge"),
                    search_dirs=[
                        _reel_img_dir,
                        *([subject_assets] if subject_assets else []),
                    ],
                    generate_kwargs={
                        "avatar_mode": "OFF",
                        "visual_role": _doc_role,
                        "negative_prompt": _act_neg,
                        "free_image_sentence": _snippet,
                        "free_image_entity": str(
                            resolved_subject_for_beat or resolved_subject or ""
                        ),
                        **_art_kwargs,
                    },
                    banned_subjects=list(_act_banned or []) or None,
                    domain_anchors=str(_act_anchors or ""),
                    prefer_stem=stem,
                    episode_subject=str(
                        resolved_subject_for_beat or resolved_subject or ""
                    ),
                )

            _gpu_baseline = 0.0
            try:
                from core.remote_gpu_manager import (  # noqa: PLC0415
                    get_manager as _get_rgpu,
                    is_remote_gpu_enabled as _rgpu_on,
                )

                if _rgpu_on("image"):
                    _gpu_baseline = float(
                        getattr(_get_rgpu().client, "total_gpu_seconds", 0) or 0
                    )
            except Exception:
                _gpu_baseline = 0.0

            _apply_still_cache_to_jobs(
                jobs=_sr_act_jobs,
                meta=_sr_act_meta,
                n_acts=_seq_n,
                start=_sr_act_start,
                work_dir=_reel_img_dir,
                stem=stem,
                extra_dirs=[subject_assets] if subject_assets else None,
                spoken_by_act={
                    i: str(_sr_act_meta[i].get("snippet") or "")
                    for i in _sr_act_meta
                },
            )

            if (
                _early_music_thread is None
                and page_ctx
                and app_config.ELEVENLABS_API_KEY
            ):
                _clips_early = Path(app_config.PAGE_OUTPUTS_DIR) / "clips"
                _clips_early.mkdir(parents=True, exist_ok=True)
                _use_v2_early = bool(getattr(page_ctx, "use_music_v2_bed", False))
                _style_early = page_ctx.ambient_music_style or "mystery"
                _dur_early = float(page_ctx.reel_duration or 105.0)
                _stem_early = f"{stem}_v{variant + 1:02d}"
                _topic_early = str(resolved_subject or "")
                _directive_early = page_ctx.music_prompt_directive_path
                _channel_early = page_ctx.page_id or ""
                _sfx_prompt_early = page_ctx.ambient_sfx_prompt or None

                def _early_music() -> None:
                    try:
                        if _use_v2_early:
                            _min = 40.0
                            try:
                                _cfg = getattr(page_ctx, "page_cfg", None) or {}
                                _min = float(_cfg.get("MUSIC_V2_MIN_SECONDS", 40.0) or 40.0)
                            except Exception:
                                _min = 40.0
                            bed, _impact = generate_master_mei_soundscape(
                                _clips_early,
                                stem=_stem_early,
                                duration_seconds=max(_min, _dur_early),
                                include_impact_sfx=False,
                                topic=_topic_early,
                                directive_path=_directive_early,
                                style_profile=_style_early,
                                channel_name=_channel_early,
                            )
                            sfx = generate_ambient_track(
                                _clips_early / f"{_stem_early}_atmosphere_sfx.mp3",
                                duration_seconds=10.0,
                                prompt=_sfx_prompt_early,
                            )
                            _early_music_box["bed"] = bed
                            _early_music_box["sfx"] = sfx
                            _early_music_box["mode"] = "v2"
                        else:
                            ambient = generate_ambient_track(
                                _clips_early / f"{_stem_early}_ambient.mp3",
                                duration_seconds=min(12.0, _dur_early),
                                prompt=_sfx_prompt_early,
                            )
                            _early_music_box["ambient"] = ambient
                            _early_music_box["mode"] = "ambient"
                    except Exception as _music_exc:  # noqa: BLE001
                        _LOG.warning("early music bed failed (%s)", _music_exc)
                        _early_music_box["mode"] = "failed"

                _early_music_thread = threading.Thread(
                    target=_early_music, name="reel-music-bed", daemon=True,
                )
                _early_music_thread.start()
                _LOG.info(
                    "ECONOMIC_REEL | music bed started during image judging | mode=%s",
                    "v2" if _use_v2_early else "ambient",
                )

            if _ladder_on:
                from modules.reel_artefact.orchestrate import run_reel_stills as _run_reel_stills

                _sr_act_results = _run_reel_stills(_sr_act_jobs)
            else:
                _sr_act_results = _run_acts_parallel(_sr_act_jobs)

            _ok_acts = 0
            from modules.reel_qa.models import StillGateResult
            from modules.reel_visual_qa import generate_and_gate as _qwen_gate

            def _accept_gate(_gate: StillGateResult, _idx: int, _text: str) -> None:
                nonlocal _ok_acts, _reel_qa_block_compile, _images_generated_this_variant
                if _gate.extra_generations:
                    _images_generated_this_variant += int(_gate.extra_generations)
                _verdict_row = dict(_gate.verdict or {})
                try:
                    if _verdict_row.get("resumed"):
                        pass
                    elif _verdict_row.get("ladder"):
                        for _spend_key, _spend_n in dict(_verdict_row.get("spend") or {}).items():
                            if int(_spend_n) > 0:
                                _image_bill[_spend_key] = (
                                    _image_bill.get(_spend_key, 0) + int(_spend_n)
                                )
                    else:
                        from modules.reel_artefact.pricing import generation_bill as _generation_bill

                        _bill_key, _bill_n = _generation_bill(
                            _gate.path,
                            int(_gate.extra_generations or 0),
                            attempts=len(_gate.attempts or []),
                        )
                        if _bill_key and _bill_n > 0:
                            _image_bill[_bill_key] = _image_bill.get(_bill_key, 0) + int(_bill_n)
                except Exception:  # noqa: BLE001
                    pass
                _row = _gate.beat_row(index=_idx, text=_text)
                _meta_row = _sr_act_meta.get(_idx) or {}
                _row["prompt"] = str(_meta_row.get("prompt") or "")
                _row["is_cta"] = bool(_meta_row.get("is_cta"))
                try:
                    from modules.reel_artefact.source import source_meta as _source_meta

                    _src = _source_meta(_row.get("image_path"))
                    _row["reference_image"] = str(_src.get("reference_image") or "")
                    _row["canonical_shape"] = str(_src.get("canonical_shape") or "")
                    _row["image_provider"] = str(_src.get("provider") or "")
                except Exception:  # noqa: BLE001
                    pass
                _beat_qa_rows.append(_row)
                if (
                    _gate.publishable
                    and not _verdict_row.get("ladder")
                    and str(_row.get("image_provider") or "").strip()
                ):
                    try:
                        from modules.reel_artefact.logbook import note_accepted as _note_accepted

                        _note_accepted(str(_row.get("image_provider") or ""))
                    except Exception:  # noqa: BLE001
                        pass
                if _gate.publishable and _gate.path is not None and Path(_gate.path).is_file():
                    try:
                        from modules.reel_grounding.still_cache import write_spoken_sidecar

                        _score = (_gate.verdict or {}).get("relevance_score")
                        write_spoken_sidecar(
                            _gate.path,
                            _text,
                            passed=True,
                            score=float(_score) if _score is not None else None,
                        )
                    except Exception:  # noqa: BLE001
                        pass
                    _sequence_image_paths.append(Path(_gate.path))
                    _ok_acts += 1
                    try:
                        from modules.reel_artefact.policy import artefact_images_enabled
                        from modules.reel_artefact.reuse import remember_approved

                        _passed_judge = bool(_verdict_row.get("is_relevant"))
                        if (
                            _ladder_on
                            and artefact_images_enabled()
                            and _passed_judge
                            and not _verdict_row.get("kept")
                        ):
                            remember_approved(
                                _gate.path,
                                kind=str(_verdict_row.get("beat_kind") or "object"),
                            )
                            if _reel_checkpoint is not None and not _verdict_row.get("resumed"):
                                _reel_checkpoint.save_beat(
                                    _idx,
                                    image=_gate.path,
                                    score=_verdict_row.get("relevance_score"),
                                    reason=str(_verdict_row.get("rejection_reason") or ""),
                                    rung=str(_verdict_row.get("rung") or ""),
                                    verdict=_verdict_row,
                                    first_pass=_gate.first_pass,
                                    kind=str(_verdict_row.get("beat_kind") or "object"),
                                )
                    except Exception:  # noqa: BLE001
                        pass
                    _LOG.info(
                        "Sequence reel | act %d/%d QA pass | beat=%.48s…",
                        _idx + 1, _seq_n, _text,
                    )
                    return
                _reused = None
                try:
                    from modules.reel_artefact.policy import artefact_images_enabled
                    from modules.reel_artefact.reuse import reuse_into

                    # The ladder already judged reuse. A second unjudged copy
                    # is what filled a reel with one Nano Banana still.
                    if (
                        _ladder_on
                        and artefact_images_enabled()
                        and not _verdict_row.get("ladder")
                    ):
                        _meta_row_reuse = _sr_act_meta.get(_idx) or {}
                        _entity_id = str(_meta_row_reuse.get("artefact_kwargs", {}).get(
                            "artefact_entity_id", ""
                        ) or "")
                        _reuse_dest = (_reel_img_dir / f"{stem}_act{_idx + 1:02d}_reuse.jpg")
                        _reused = reuse_into(_reuse_dest, _idx, entity_id=_entity_id)
                except Exception:  # noqa: BLE001
                    _reused = None
                if _reused is not None and Path(_reused).is_file():
                    _row["image_path"] = str(_reused)
                    _row["passed"] = True
                    _row["reuse"] = "approved"
                    _sequence_image_paths.append(Path(_reused))
                    _ok_acts += 1
                    _LOG.warning(
                        "REEL QA REUSE | act %d/%d reason=%s — approved still kept the reel",
                        _idx + 1, _seq_n, _gate.blocked_reason or "rejected",
                    )
                    return
                if (
                    _gate.blocked_reason == "pool_exhausted"
                    and _sequence_image_paths
                    and Path(_sequence_image_paths[-1]).is_file()
                ):
                    # s23d B #10: no fitting photo left. Hold the previous
                    # beat's photo rather than a flat placeholder or a site
                    # the line never names.
                    _held = Path(_sequence_image_paths[-1])
                    _row["image_path"] = str(_held)
                    _row["passed"] = True
                    _row["reuse"] = "hold_previous"
                    _sequence_image_paths.append(_held)
                    _ok_acts += 1
                    _LOG.warning(
                        "REEL QA HOLD | act %d/%d reason=pool_exhausted — previous photo held",
                        _idx + 1, _seq_n,
                    )
                    return
                _guaranteed = None
                try:
                    from modules.reel_artefact.reuse import guarantee_still

                    _meta_fallback = _sr_act_meta.get(_idx) or {}
                    _entity_fallback = str(_meta_fallback.get("artefact_kwargs", {}).get(
                        "artefact_entity_id", ""
                    ) or "")
                    _fallback_dest = _reel_img_dir / f"{stem}_act{_idx + 1:02d}_fallback.jpg"
                    _guaranteed = guarantee_still(
                        _fallback_dest,
                        _idx,
                        entity_id=_entity_fallback,
                        n_beats=_seq_n,
                    )
                except Exception as _fall_exc:  # noqa: BLE001
                    _LOG.warning("REEL QA FALLBACK failed (%s)", _fall_exc)
                    _guaranteed = None
                if _guaranteed is not None and Path(_guaranteed).is_file():
                    _row["image_path"] = str(_guaranteed)
                    _row["passed"] = True
                    _row["reuse"] = "fallback"
                    _sequence_image_paths.append(Path(_guaranteed))
                    _ok_acts += 1
                    _LOG.warning(
                        "REEL QA FALLBACK | act %d/%d reason=%s — still kept, reel continues",
                        _idx + 1, _seq_n, _gate.blocked_reason or "rejected",
                    )
                    return
                _reel_qa_block_compile = True
                _LOG.error(
                    "REEL QA BLOCK | act %d/%d reason=%s — no substitute still",
                    _idx + 1, _seq_n, _gate.blocked_reason or "rejected",
                )

            for _act_i in range(_sr_act_start, _seq_n):
                _meta = _sr_act_meta[_act_i]
                _snippet = _meta["snippet"]
                _cached_still = _meta.get("cached_path")
                if _cached_still is not None:
                    from modules.reel_qa.relevance import (
                        evaluate_chunk_relevance as _score_still,
                    )
                    from modules.reel_qa.relevance import (
                        evaluate_with_technical_retry as _score_retry,
                    )

                    _cached_verdict = _score_retry(
                        _score_still,
                        _cached_still,
                        _snippet,
                        str(_meta.get("prompt") or ""),
                        banned_subjects=list(_meta.get("banned_subjects") or []) or None,
                    )
                    from modules.reel_qa.relevance import verdict_passed as _verdict_passed

                    if (
                        _verdict_passed(_cached_verdict)
                        and _cached_verdict.get("qa_status") != "unavailable"
                        and Path(_cached_still).is_file()
                    ):
                        _accept_gate(
                            StillGateResult(
                                path=Path(_cached_still),
                                extra_generations=0,
                                publishable=True,
                                attempts=[_cached_verdict],
                                verdict=_cached_verdict,
                            ),
                            _act_i,
                            _snippet,
                        )
                        continue
                    if _cached_verdict.get("qa_status") == "unavailable":
                        _accept_gate(
                            StillGateResult(
                                path=None,
                                extra_generations=0,
                                publishable=False,
                                blocked_reason=str(
                                    _cached_verdict.get("rejection_reason") or "qa_unavailable"
                                ),
                                attempts=[_cached_verdict],
                                verdict=_cached_verdict,
                            ),
                            _act_i,
                            _snippet,
                        )
                        continue
                    if _ladder_on:
                        _cached_miss = _sr_act_results.get(_act_i)
                        if isinstance(_cached_miss, StillGateResult):
                            _accept_gate(_cached_miss, _act_i, _snippet)
                            continue
                    _regen = _qwen_gate(
                        _meta.get("generate_fn") or adapter.generate,
                        prompt=str(_meta.get("prompt") or _snippet),
                        chunk_text=_snippet,
                        output_stem=f"{_meta.get('stem') or 'act'}_r01",
                        output_directory=_reel_img_dir,
                        channel=(page_ctx.page_id or "ancient_knowledge"),
                        generate_kwargs={
                            "avatar_mode": "OFF",
                            "negative_prompt": _meta.get("negative_prompt"),
                            "free_image_sentence": _snippet,
                            "free_image_entity": str(
                                _meta.get("episode_subject") or resolved_subject or ""
                            ),
                            **dict(_meta.get("artefact_kwargs") or {}),
                        },
                        banned_subjects=list(_meta.get("banned_subjects") or []) or None,
                        domain_anchors=str(_meta.get("domain_anchors") or ""),
                        episode_subject=str(
                            _meta.get("episode_subject") or resolved_subject or ""
                        ),
                    )
                    _accept_gate(_regen, _act_i, _snippet)
                    continue
                _result = _sr_act_results.get(_act_i)
                if isinstance(_result, Exception):
                    _accept_gate(
                        StillGateResult(
                            path=None,
                            extra_generations=0,
                            publishable=False,
                            blocked_reason=f"generate_failed:{type(_result).__name__}",
                        ),
                        _act_i,
                        _snippet,
                    )
                    continue
                if isinstance(_result, StillGateResult):
                    _accept_gate(_result, _act_i, _snippet)
                    continue
                _accept_gate(
                    StillGateResult(
                        path=None,
                        extra_generations=0,
                        publishable=False,
                        blocked_reason="qa_result_missing",
                    ),
                    _act_i,
                    _snippet,
                )
            # One GPU bill for the parallel batch (sum of per-job seconds).
            _gpu_delta = _track_remote_gpu_batch(
                cost_tracker, seconds_before=_gpu_baseline, jobs=_ok_acts,
            )
            if cost_tracker is not None:
                try:
                    from modules.reel_artefact.policy import artefact_images_enabled

                    if artefact_images_enabled() and _image_bill and (
                        _ladder_on or _asset_pool
                    ):
                        for _bill_key, _bill_n in _image_bill.items():
                            if int(_bill_n) > 0:
                                cost_tracker.track_image(
                                    model_key=_bill_key,
                                    count=int(_bill_n),
                                )
                    elif _ok_acts and not _asset_pool:
                        cost_tracker.track_image(
                            model_key="image_flux_schnell", count=_ok_acts,
                        )
                except Exception:  # noqa: BLE001
                    if _ok_acts and not _asset_pool:
                        cost_tracker.track_image(
                            model_key="image_flux_schnell", count=_ok_acts,
                        )
            if _ok_acts:
                _images_generated_this_variant += _ok_acts

            _LOG.info(
                "REEL VisualQA | still gate applied | blocked=%s beats=%d frames=%d",
                _reel_qa_block_compile,
                len(_beat_qa_rows),
                len(_sequence_image_paths),
            )
            try:
                from modules.reel_artefact.logbook import summary_line as _artefact_summary
                from modules.reel_artefact.policy import artefact_images_enabled as _art_on

                if _art_on():
                    _LOG.info(_artefact_summary())
            except Exception:  # noqa: BLE001
                pass

        if _sequence_image_paths:
            if not img_path_display:
                img_path_display = _sequence_image_paths[0]
            _LOG.info(
                "Sequence reel | frames=%d | api_image_units=%d",
                len(_sequence_image_paths),
                _images_generated_this_variant,
            )
            if post_type == "WAN_REEL":
                _wan_stage_times["script_tts_stills_s"] = round(
                    time.monotonic() - _wan_t0_variant, 2
                )
                _LOG.info(
                    "WAN_REEL | script+TTS+stills wall=%.1fs | frames=%d",
                    _wan_stage_times["script_tts_stills_s"],
                    len(_sequence_image_paths),
                )

        # ── Visual Control Agent (Master Mei) — VLM + phash before compile ──
        if (
            _is_mm
            and _sequence_image_paths
            and adapter is not None
            and len(_sequence_image_paths) >= 2
        ):
            try:
                from agents.media.visual_inspector import inspect_sequence_images
                from agents.media.visual_roles import (
                    ROLE_MASTER,
                    assign_frame_beats,
                    build_role_prompt,
                )

                _qa_timeout = float(
                    getattr(app_config, "VISUAL_EVAL_TIMEOUT_S", 10) or 10
                )
                # Inner VLM already hard-caps at VISUAL_EVAL_TIMEOUT_S; give
                # phash a couple of extra seconds so fail-open notes propagate.
                _insp_wall = max(12.0, _qa_timeout + 2.0)
                _insp_pool = ThreadPoolExecutor(max_workers=1)
                try:
                    _insp = _insp_pool.submit(
                        inspect_sequence_images,
                        _sequence_image_paths,
                        use_vlm=True,
                    ).result(timeout=_insp_wall)
                except Exception as _insp_to:  # noqa: BLE001
                    _why = str(_insp_to).strip() or type(_insp_to).__name__
                    _LOG.warning(
                        "Visual Inspector non-blocking: timed out/failed (%s) — proceeding to render",
                        _why,
                    )
                    _insp = None
                finally:
                    _insp_pool.shutdown(wait=False, cancel_futures=True)
                if _insp is None:
                    _LOG.info("VISUAL_INSPECTOR | skipped (fail-open) — compiling as-is")
                    _insp_notes = []
                    _insp_regen: list[int] = []
                else:
                    _insp_notes = list(_insp.notes or [])
                    _insp_regen = list(_insp.regenerate_indices or [])
                    _LOG.info(
                        "VISUAL_INSPECTOR | passed=%s regen_frames=%s | %s",
                        _insp.passed,
                        [i + 1 for i in _insp_regen],
                        "; ".join(_insp_notes[:4]) if _insp_notes else "ok",
                    )
                if any("visual qa skipped" in (n or "").lower() for n in _insp_notes):
                    _insp_regen = []
                _beats_insp = assign_frame_beats(len(_sequence_image_paths))
                for _ri in _insp_regen[:5]:  # cap regen attempts
                    if _ri < 0 or _ri >= len(_sequence_image_paths):
                        continue
                    _beat = _beats_insp[_ri] if _ri < len(_beats_insp) else ""
                    _role = (
                        "master"
                        if _beat in ("intro", "outro", "training")
                        else ("disciple" if _beat == "break_free" else "slave")
                    )
                    _snip = (
                        _spoken_snippets[_ri]
                        if _ri < len(_spoken_snippets)
                        else resolved_subject
                    )
                    _pos, _neg = build_role_prompt(
                        role=_role,
                        visual_dna=(
                            (page_ctx.master_mei_visual_dna or _MEI_DNA_FALLBACK)
                            if _role == ROLE_MASTER
                            else ""
                        ),
                        spoken_beat=_snip,
                        subject=resolved_subject,
                        act_index=_ri,
                        beat=_beat,
                    )
                    _av_ref = locals().get("_mm_avatar_ref")
                    _av_w = locals().get("_mm_avatar_w")
                    _use_av = _role == ROLE_MASTER and _av_ref is not None
                    try:
                        _regen_path = adapter.generate(
                            _pos,
                            output_stem=f"{stem}_act{_ri + 1:02d}_regen",
                            output_directory=_reel_img_dir,
                            reference_image_path=_av_ref if _use_av else None,
                            avatar_mode="ON" if _use_av else "OFF",
                            reference_image_weight=_av_w if _use_av else None,
                            visual_role=_role,
                            negative_prompt=_neg or None,
                        )
                        _sequence_image_paths[_ri] = _ensure_sequence_image(
                            _regen_path,
                            fallback=_sequence_image_paths[_ri],
                            target_path=_reel_img_dir / f"{stem}_act{_ri + 1:02d}_regen.png",
                        )
                        _images_generated_this_variant += _track_adapter_image(
                            cost_tracker, adapter
                        )
                        _LOG.info(
                            "VISUAL_INSPECTOR | regenerated frame %d (beat=%s role=%s)",
                            _ri + 1, _beat, _role,
                        )
                    except Exception as _regen_exc:  # noqa: BLE001
                        _LOG.warning(
                            "VISUAL_INSPECTOR | regen frame %d failed: %s",
                            _ri + 1, _regen_exc,
                        )
            except Exception as _insp_exc:  # noqa: BLE001
                _LOG.warning("VISUAL_INSPECTOR skipped: %s", _insp_exc)

    posting_slot_display = scheduled_bulk_post_display(variant_index=variation_index)

    # ====================================================================
    # PHASE B2: STANDARD_QUOTE caption (SMART_BAIT already done in B1)
    # ====================================================================

    # ---- Caption: STANDARD_QUOTE path -----------------------------------
    # SMART_BAIT, LONG_CAPTION_IMAGE, ECONOMIC_REEL, and CAROUSEL all generate captions in Phase B1.
    # CTA_CAPTION_IMAGE uses this original Anna research + humanize path (no copyright footer).
    if post_type not in ("SMART_BAIT", "LONG_CAPTION_IMAGE", "ECONOMIC_REEL", "WAN_REEL", "CAROUSEL") and not skip_caption:
        assert caption_engine is not None
        caption_mode_tag = "humanized"

        batch_narrative = (
            pre_narratives[variation_index]
            if pre_narratives
            and variation_index < len(pre_narratives)
            and pre_narratives[variation_index]
            else ""
        )

        if batch_narrative:
            raw_sheet = batch_narrative
            _LOG.debug(
                "Variant %s: using pre-computed batch narrative (%d chars).",
                variant + 1,
                len(raw_sheet),
            )
        else:
            try:
                raw_sheet = caption_engine.synthesize_facts(
                    resolved_subject,
                    corpus,
                    research_model_override=econ_model if (economic and not caption_engine._deepseek) else None,
                    variation_index=variation_index,
                    total_variants=qty,
                    economic=economic,
                )
            except Exception as rex:  # noqa: BLE001
                attempted_rid = caption_engine.research_primary_id
                _LOG.error(
                    "Gemini research failed variant=%s Gemini_head=`%s`",
                    variant + 1,
                    attempted_rid,
                    exc_info=True,
                )
                logging.error(
                    "VARIANT_FAIL | synthesize_facts | variant=%s | err=%s",
                    variant + 1,
                    rex,
                )
                raise

        # Durability checkpoint: save research output before humanization.
        # imgbb_url starts empty here; updated after brand compositing + upload.
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        durable_fname = f"post_{stamp}_v{variant + 1:02d}.json"
        durable_abs = app_config.LIBRARY_DIR / durable_fname
        created_iso = datetime.now(timezone.utc).isoformat()
        excel_rel = path_under_engine(app_config.ENGINE_ROOT, app_config.POST_PLANNER_XLSX)

        pending_payload: dict[str, Any] = {
            "page_id": page_ctx.page_id if page_ctx else app_config.ACTIVE_PAGE,
            "avatar_mode": avatar_mode,
            "post_format": post_format,
            "topic": resolved_subject,
            "subject_slug": slug,
            "variant_index": variant + 1,
            "quantity_total": qty,
            "economic_brain_mode": economic,
            "image_relative": img_ref_engine,
            "video_path": video_path_str,
            "imgbb_url": "",
            "library_relative": path_under_engine(app_config.ENGINE_ROOT, durable_abs),
            "excel_relative": excel_rel,
            "raw_fact_sheet": raw_sheet,
            "humanized_caption": PENDING_CAPTION,
            "caption_status": "pending",
            "created_utc": created_iso,
        }
        write_atomic_json(durable_abs, pending_payload)

        caption = ""
        if raw_sheet:
            caption, caption_mode_tag = caption_engine.humanize_voice_with_fallback(
                raw_sheet,
                resolved_subject,
                variation_index=variation_index,
                total_variants=qty,
                cta_keyword=cta_kw,
                economic=economic,
                model_id=econ_model if (economic and not caption_engine._deepseek) else None,
                cta_enabled=cta_enabled,
                cta_caption_format=(post_type == "CTA_CAPTION_IMAGE"),
            )
        else:
            caption_mode_tag = "researcher_fallback"

    # ====================================================================
    # PHASE C: Brand compositing  (after overlay_text is known)
    # ====================================================================
    logo_path: Path | None = (
        page_ctx.logo_png if (page_ctx and page_ctx.logo_exists) else None
    )
    # Determine compositing mode:
    # - SMART_BAIT: full 4-layer stack (bg + mask + text + logo) unless --text-overlay OFF
    # - LONG_CAPTION_IMAGE / CTA_CAPTION_IMAGE / CAROUSEL: clean still (logo only)
    #   unless the operator explicitly passes --text-overlay ON / --gframe ON
    # - IMAGE_BACKGROUND / IMAGE_QUOTE / TEXT_QUOTE: text overlay if overlay_text present
    _is_long_caption_image = _is_logo_only_still(post_type)
    _is_economic_reel      = _is_sequence_video_post(post_type)
    _is_wan_reel           = (post_type == "WAN_REEL")
    _page_overlay_flag = None
    if page_ctx is not None and "ENABLE_TEXT_OVERLAY" in (page_ctx.page_cfg or {}):
        _page_overlay_flag = bool(page_ctx.page_cfg.get("ENABLE_TEXT_OVERLAY"))
    # Precedence: CLI --text-overlay/--gframe > page ENABLE_TEXT_OVERLAY >
    # post-type default (CAROUSEL / logo-only stills stay clean).
    _clean_still = post_type in ("CAROUSEL", "LONG_CAPTION_IMAGE", "CTA_CAPTION_IMAGE")
    if text_overlay_enabled is False:
        _overlay_on = False
    elif text_overlay_enabled is True:
        _overlay_on = True
    elif _clean_still and _page_overlay_flag is False:
        _overlay_on = False
    elif _clean_still and _page_overlay_flag is True:
        _overlay_on = True
    elif _clean_still or _is_long_caption_image or _is_economic_reel:
        _overlay_on = False
    else:
        _overlay_on = True
    _needs_text_overlay = (
        _overlay_on
        and not _is_economic_reel
        and post_format in ("IMAGE_BACKGROUND", "IMAGE_QUOTE", "TEXT_QUOTE", "IMAGE_AVATAR")
    )

    # --- Resolve per-page font variables (path, size scale, colour, outline) ---
    _font_size_scale: float = page_ctx.font_size_scale if page_ctx else 0.08
    _font_color: tuple[int, int, int] = page_ctx.font_color if page_ctx else (255, 255, 255)
    _text_outline_width: int = page_ctx.text_outline_width if page_ctx else 1
    _font_path_abs: str = ""
    if page_ctx and page_ctx.font_path:
        _fp_candidate = app_config.ENGINE_ROOT / page_ctx.font_path
        if _fp_candidate.is_file():
            _font_path_abs = str(_fp_candidate)
        else:
            _LOG.debug(
                "Page font not found at '%s' — brand_composer will use system pool.", _fp_candidate
            )
    # Resolve per-page logo layout variables (size + corner position)
    _logo_size_scale: float = page_ctx.logo_size_scale if page_ctx else 0.18
    _logo_position: str     = page_ctx.logo_position   if page_ctx else "bottom_right"

    if raw_bg_path is not None and raw_bg_path.is_file():
        if _is_long_caption_image:
            # LONG_CAPTION_IMAGE / CTA_CAPTION_IMAGE:
            #   Layer 1 — background (Gemini/Flux)
            #   Layer 4 — logo watermark baked in (static image shared as-is)
            #   Caption lives in the post text, not on the image.
            if logo_path:
                img_path_display = _brand_apply_logo(
                    raw_bg_path, logo_path,
                    position=_logo_position,
                    size_scale=_logo_size_scale,
                )
            # else: leave as raw background
        elif _is_economic_reel:
            # ECONOMIC_REEL: background PNG must remain 100% logo-free.
            # The logo watermark must NOT be baked into this image because it
            # would participate in the Ken Burns zoom and appear to drift.
            # video_engine.compile_dynamic_reel() handles brand identity by
            # compositing a completely static brand_label RGBA layer AFTER
            # every zoom transform — it never moves with the background.
            img_path_display = raw_bg_path   # pass clean PNG directly to video engine
        elif _needs_text_overlay and overlay_text:
            # SMART_BAIT / IMAGE_BACKGROUND / IMAGE_QUOTE / TEXT_QUOTE: full 4-layer stack
            img_path_display = _brand_apply_text(
                raw_bg_path, overlay_text,
                logo_path=logo_path,
                page_id=page_ctx.page_id if page_ctx else None,
                logo_size_scale=_logo_size_scale,
                logo_position=_logo_position,
                font_path_override=_font_path_abs or None,
                font_size_scale=_font_size_scale,
                font_color=_font_color,
                text_outline_width=_text_outline_width,
                post_type=post_type,
            )
        elif logo_path:
            # IMAGE_AVATAR or any other format: logo watermark only
            img_path_display = _brand_apply_logo(
                raw_bg_path, logo_path,
                position=_logo_position,
                size_scale=_logo_size_scale,
            )
        # else: img_path_display stays as raw background (no compositing)

        # HYBRID_VIDEO: burn text rock-solid while background animates
        if video_path_str:
            try:
                final_video = _brand_burn_video(
                    Path(video_path_str),
                    overlay_text,
                    logo_path=logo_path,
                )
                video_path_str = str(final_video)
            except Exception as vexc:  # noqa: BLE001
                _LOG.warning("Video text burn exception (variant %s): %s", variant + 1, vexc)

    # ====================================================================
    # PHASE D: ECONOMIC_REEL video compilation
    #   Triggered only when post_type == "ECONOMIC_REEL".
    #   Uses the brand-composited image (with text + logo already burned in)
    #   as the video background, then layers ElevenLabs voiceover + ambient.
    # ====================================================================
    reel_path: Path | None = None
    _b2_video_url: str = ""  # populated after successful B2 upload of the reel

    if _is_economic_reel and _asset_pool and len(_sequence_image_paths) >= 2:
        try:
            from modules.reel_artefact.asset_pool import unique_stills as _unique_stills

            _sequence_image_paths, _stuck_beats = _unique_stills(
                list(_sequence_image_paths),
                list(_asset_pool),
                flux_generate=getattr(adapter, "generate", None) if adapter else None,
                geography=_pool_geography,
            )
            if _stuck_beats:
                _LOG.warning("REPEAT GUARD | unresolved beats=%s", _stuck_beats)
        except Exception as _ug_exc:  # noqa: BLE001
            _LOG.warning("REPEAT GUARD skipped (%s)", type(_ug_exc).__name__)

    # ── Sequential scene export + optional human approval gate ─────────────
    # Persist scene_01.png…scene_N.png under:
    #   outputs/<page>/assets/<episode_id>/
    # Clips always render to outputs/<page>/clips/
    if _is_economic_reel:
        if _episode_assets_dir is None:
            _episode_assets_dir = Path(app_config.ASSETS_DIR) / _episode_id
            _episode_assets_dir.mkdir(parents=True, exist_ok=True)
        _export_sources: list = (
            list(_sequence_image_paths)
            if _sequence_image_paths
            else (
                [Path(img_path_display)]
                if img_path_display and Path(img_path_display).is_file()
                else []
            )
        )
        if _export_sources:
            _exported_scenes = _export_scene_assets_sequential(
                _export_sources, _episode_assets_dir,
            )
            if _exported_scenes:
                _assets_dir_disp = str(_episode_assets_dir.resolve())
                print(f"\n[ASSETS] Episode images saved -> {_assets_dir_disp}")
                _LOG.info(
                    "Scene assets exported → %s (%d files) | episode=%s",
                    _assets_dir_disp, len(_exported_scenes), _episode_id,
                )
                if _sequence_image_paths:
                    # Resume render from the approved canonical scene assets
                    _sequence_image_paths = list(_exported_scenes)
                elif img_path_display:
                    img_path_display = str(_exported_scenes[0])
                if render_approval_required:
                    # Serialize pause across concurrent workers (stdin is shared)
                    with write_lock:
                        input(
                            f"\n [PAUSED] Images saved to episode assets folder:\n"
                            f" {_assets_dir_disp}\n"
                            " Press [ENTER] to approve and continue video rendering, "
                            "or [Ctrl+C] to abort...\n"
                        )
                    _LOG.info(
                        "Render approval granted — continuing video compilation "
                        "(variant %s | episode=%s).",
                        variant + 1, _episode_id,
                    )

    if _reel_qa_block_compile and _sequence_image_paths:
        _LOG.warning(
            "REEL QA | some beats were hard — compiling the reel anyway"
        )
        _reel_qa_block_compile = False
    elif _reel_qa_block_compile:
        _LOG.error(
            "REEL QA | skipping compile — no stills at all"
        )
        print("[VisualQA] compile skipped — no stills")
    if _is_economic_reel and not _reel_qa_block_compile and (
        (img_path_display and Path(img_path_display).is_file())
        or bool(_sequence_image_paths)
    ):
        _LOG.info("ECONOMIC_REEL | Launching video compilation pipeline (variant %s)…", variant + 1)
        if not locals().get("_ak_voice_ready"):
            _voice_path = None
        _ambient_path: Path | None = None

        # Always target the page clips/ folder (never loose root / episode clips)
        _reel_dir = Path(app_config.PAGE_OUTPUTS_DIR) / "clips"
        os.makedirs(_reel_dir, exist_ok=True)

        # -- Voiceover (ElevenLabs TTS + word-level timestamps for auto-subtitles) --
        # caption      = 4-sentence narration script spoken by Dorothy voice
        # overlay_text = short on-screen hook headline (static layer, NOT spoken)
        # Guard: never pass the "(skipped)" initialisation placeholder to TTS.
        _real_caption = caption if caption and caption != "(skipped)" else ""
        _voiceover_script = _real_caption or overlay_text

        # For sequence reels the caption is a short social-media post body.
        # Generate a longer narration for TTS — or reuse the early narrative
        # script already built for spoken-beat / image-sync matching.
        #
        # Round 7 (Measure-Then-Correct 2026-08-15): if the AK voice track
        # is already committed (``_ak_voice_ready``), the early script IS
        # the final script — reuse verbatim without any word-count gate.
        # The old fallback used to regenerate here whenever the early
        # draft came in below ``page_ctx.reel_narration_min_words``, but
        # word count is no longer a correctness criterion; the audio has
        # already been synthesised and measured, so a new script would
        # only be wasted API cost without changing the reel one bit.
        if (
            page_ctx is not None
            and page_ctx.enable_sequence_reel
            and caption_engine is not None
            and resolved_subject
        ):
            try:
                if locals().get("_ak_voice_ready") and _early_seq_script:
                    _seq_script = _early_seq_script
                    _voiceover_script = _seq_script
                    _LOG.info(
                        "SEQUENCE_REEL | reusing AK-committed voice script "
                        "(%d words) — audio already synthesised.",
                        len(_seq_script.split()),
                    )
                elif _early_seq_script and len(_early_seq_script.split()) >= 40:
                    _seq_script = _early_seq_script
                    _voiceover_script = _seq_script
                    _commit_reel_angle(
                        caption_engine, page_ctx, resolved_subject, _seq_script,
                    )
                    _LOG.info(
                        "SEQUENCE_REEL | reusing early narrative script "
                        "(%d words) — no regen needed.",
                        len(_seq_script.split()),
                    )
                else:
                    # Fallback: early script missing entirely — synthesise one.
                    _LOG.info(
                        "SEQUENCE_REEL | generating long-form voiceover script for '%s' (voice pref: %s)",
                        resolved_subject, page_ctx.tts_voice_preference or "default",
                    )
                    _words_tgt = page_ctx.reel_narration_words
                    _n_for_script = max(
                        2,
                        len(_sequence_image_paths) if _sequence_image_paths else page_ctx.reel_image_count,
                    )
                    _seq_script = caption_engine.generate_sequence_voiceover(
                        resolved_subject,
                        page_niche=page_ctx.content_niche,
                        persona_voice=page_ctx.tts_voice_preference or (
                            "calm, deep, highly wise, authoritative ancient master"
                            if (page_ctx.page_id or "").lower() == "master_mei"
                            else "investigative, neutral, immersive"
                        ),
                        n_acts=_n_for_script,
                        duration_s=page_ctx.reel_duration,
                        total_words_target=_words_tgt,
                        economic=economic,
                        niche_disclaimer=page_ctx.niche_disclaimer,
                        cta_line="",
                        narrative_mode=page_ctx.narrative_mode,
                        batch_angle_block=_batch_angle_block,
                        previously_generated_hooks=list(generated_hooks_cache or []),
                    )
                    if cost_tracker is not None and _seq_script:
                        cost_tracker.track_text(char_count=len(_seq_script))
                    if _seq_script and len(_seq_script.split()) >= 40:
                        _voiceover_script = _seq_script
                        _commit_reel_angle(
                            caption_engine, page_ctx, resolved_subject, _seq_script,
                        )
                        _LOG.info(
                            "SEQUENCE_REEL | fallback voiceover generated: %d words",
                            len(_seq_script.split()),
                        )
            except Exception as _seq_exc:  # noqa: BLE001
                _LOG.warning("SEQUENCE_REEL | voiceover generation failed: %s — using caption.", _seq_exc)
        _word_timings: list[tuple[str, float, float]] = []
        if locals().get("_ak_voice_ready") and _voice_path is not None:
            _word_timings = list(locals().get("_narr_word_timings") or [])
            _cta_text = (page_ctx.reel_cta_text if page_ctx else "") or ""
            if _cta_text and float(locals().get("_narr_dur") or 0) > 0:
                _cta_offset = float(_narr_dur) + 1.0
                _cta_words = _cta_text.split()
                _cta_slot = 3.5 / max(1, len(_cta_words))
                _word_timings = _word_timings + [
                    (w, _cta_offset + i * _cta_slot, _cta_offset + (i + 1) * _cta_slot)
                    for i, w in enumerate(_cta_words)
                ]
            _LOG.info(
                "SEQUENCE_REEL | reusing pre-image TTS | narr=%.1fs | %s",
                float(locals().get("_narr_dur") or 0),
                Path(_voice_path).name,
            )
        elif _voiceover_script and app_config.ELEVENLABS_API_KEY:
            # 1. Trim narration body to page word target (master_mei ≈ 175 for ~75 s).
            #    Allow slight headroom so we don't over-trim a correctly long script.
            _max_words = (page_ctx.reel_narration_words if page_ctx else 140) + 30
            if page_ctx and (page_ctx.page_id or "").lower() in ("master_mei", "ancient_knowledge"):
                # Hard cap 260 per Master Philosophical Scriptwriter brief
                _max_words = int(page_ctx.reel_narration_max_words)
            _voiceover_script = _trim_script_to_word_limit(_voiceover_script, max_words=_max_words)
            _is_mei_page = (page_ctx.page_id if page_ctx else "").lower() == "master_mei"
            _tts_ssml = bool(page_ctx.tts_enable_ssml) if page_ctx else False
            if _is_mei_page:
                # Purge emotion tags; keep strategic <break time="1.5s"/> for 100–120 s pacing
                from agents.media.mei_narrative import (
                    prepare_mei_tts_text,
                    strip_inline_follow_cta,
                )
                _pre = _voiceover_script
                _voiceover_script = prepare_mei_tts_text(_voiceover_script)
                _voiceover_script = strip_inline_follow_cta(_voiceover_script)
                if _pre != _voiceover_script:
                    _LOG.info(
                        "MASTER_MEI TTS prep | POV/blacklist + break pauses (%d → %d chars, breaks=%d)",
                        len(_pre),
                        len(_voiceover_script),
                        _voiceover_script.lower().count("<break"),
                    )
                # Honor page_config TTS_ENABLE_SSML (True → send break pauses to ElevenLabs)
                _tts_ssml = bool(page_ctx.tts_enable_ssml) if page_ctx else True
            elif page_ctx is None or page_ctx.strip_audio_tags_before_tts:
                # Other pages: strip bracketed emotional tags so ElevenLabs never reads them aloud
                _pre_strip = _voiceover_script
                _voiceover_script = _strip_audio_behavior_tags(_voiceover_script)
                if _pre_strip != _voiceover_script:
                    _LOG.info(
                        "TTS tag strip | removed bracket tags before ElevenLabs (%d → %d chars)",
                        len(_pre_strip), len(_voiceover_script),
                    )
            _narration_out = _reel_dir / f"{stem}_v{variant + 1:02d}_narration.mp3"
            _voice_id = page_ctx.elevenlabs_voice_id if page_ctx else None
            _tts_speed = page_ctx.tts_narration_speed if page_ctx else None
            _tts_vs = page_ctx.elevenlabs_voice_settings if page_ctx else None
            _tts_expressive = (
                page_ctx.tts_expressive_mode
                if page_ctx and hasattr(page_ctx, "tts_expressive_mode")
                else ((page_ctx.page_id or "").lower() == "master_mei" if page_ctx else False)
            )
            _tts_script = _voiceover_script
            if (page_ctx.page_id if page_ctx else "").lower() == "ancient_knowledge":
                from modules.reel_artefact.speech import spoken_numbers as _spoken_numbers

                _tts_script = _spoken_numbers(_voiceover_script)
            try:
                _voice_path, _word_timings = generate_voiceover_with_timestamps(
                    _tts_script,
                    _narration_out,
                    voice_id=_voice_id or None,
                    model_id=page_ctx.elevenlabs_model if page_ctx else "eleven_v3",
                    speed=_tts_speed,
                    voice_settings=_tts_vs or None,
                    enable_ssml=_tts_ssml,
                    expressive_mode=_tts_expressive,
                )
                _word_timings = _filter_audio_tag_timings(_word_timings)
            except Exception as vaudio_exc:  # noqa: BLE001
                _ak_must_speak = (
                    (page_ctx.page_id if page_ctx else "").lower() == "ancient_knowledge"
                )
                if _ak_must_speak:
                    raise RuntimeError(
                        f"Voiceover generation failed (variant {variant + 1}): "
                        f"{vaudio_exc} — refusing silent ancient_knowledge compile."
                    ) from vaudio_exc
                _LOG.warning(
                    "Voiceover generation failed (variant %s): %s — reel will be silent.",
                    variant + 1, vaudio_exc,
                )

            # 2. Generate CTA audio as a distinct block (strict zero-overlap rule).
            #    Use generate_voiceover_with_timestamps so subtitle coverage extends
            #    across the full stitched track (narration + silence + CTA).
            _cta_text = (page_ctx.reel_cta_text if page_ctx else "") or ""
            from agents.media.mei_narrative import approved_cta_text, fix_cta_typos
            if (page_ctx.page_id if page_ctx else "").lower() == "master_mei":
                # Force the single approved CTA — never invent or duplicate variants
                _cta_text = approved_cta_text()
            if page_ctx is None or page_ctx.strip_audio_tags_before_tts:
                _cta_text = _strip_audio_behavior_tags(_cta_text)
            # Always correct sovereianty → sovereignty before TTS / burn-in
            _cta_text = fix_cta_typos(_cta_text or "")
            _cta_path: "Path | None" = None
            _cta_word_timings: list[tuple[str, float, float]] = []
            if _cta_text and _voice_path is not None:
                _cta_out = _reel_dir / f"{stem}_v{variant + 1:02d}_cta.mp3"
                try:
                    _is_mei_cta = (
                        (page_ctx.page_id if page_ctx else "").lower() == "master_mei"
                    )
                    if _is_mei_cta:
                        # Credit optimization: synthesize CTA once, reuse cache.
                        from agents.media.audio_engine import (
                            default_cta_cache_path,
                            ensure_cached_cta_voiceover,
                        )
                        import shutil as _shutil_cta

                        _cache = ensure_cached_cta_voiceover(
                            _cta_text,
                            default_cta_cache_path(),
                            voice_id=_voice_id or None,
                            model_id=page_ctx.elevenlabs_model if page_ctx else "eleven_v3",
                            speed=_tts_speed,
                            voice_settings=_tts_vs or None,
                            expressive_mode=_tts_expressive,
                        )
                        if _cache is not None and Path(_cache).is_file():
                            _shutil_cta.copy2(str(_cache), str(_cta_out))
                            _cta_path = _cta_out
                            # Approximate CTA subtitle window from clip duration
                            try:
                                from moviepy import AudioFileClip as _AFC_CTA
                                with _AFC_CTA(str(_cta_path)) as _cta_afc:
                                    _cta_dur_est = float(_cta_afc.duration or 3.0)
                            except Exception:
                                _cta_dur_est = 3.0
                            _cta_words = _cta_text.split()
                            if _cta_words:
                                _slot = _cta_dur_est / max(1, len(_cta_words))
                                _cta_word_timings = [
                                    (w, i * _slot, (i + 1) * _slot)
                                    for i, w in enumerate(_cta_words)
                                ]
                            _LOG.info(
                                "CTA cache used | %s (%d chars) — ElevenLabs skip on hit",
                                _cta_path.name, len(_cta_text),
                            )
                        else:
                            raise RuntimeError("CTA cache unavailable")
                    else:
                        _cta_path, _cta_word_timings = generate_voiceover_with_timestamps(
                            _cta_text,
                            _cta_out,
                            voice_id=_voice_id or None,
                            model_id=page_ctx.elevenlabs_model if page_ctx else "eleven_v3",
                            speed=_tts_speed,
                            voice_settings=_tts_vs or None,
                            expressive_mode=_tts_expressive,
                        )
                        _cta_word_timings = _filter_audio_tag_timings(_cta_word_timings)
                        _LOG.info(
                            "CTA voiceover generated | %s (%d chars)",
                            _cta_path.name, len(_cta_text),
                        )
                except Exception as _cta_exc:  # noqa: BLE001
                    _LOG.warning("CTA voiceover failed (%s) — CTA will be omitted.", _cta_exc)

            # 3. Stitch: narration + 1.0 s silence + CTA → single voice track.
            #    After stitching, offset CTA word timings by (narration_dur + 1.0 s)
            #    and append to _word_timings so subtitles cover the whole track.
            if _voice_path is not None and _cta_path is not None:
                # Capture narration duration from the REAL returned path
                # (remote F5 may write .flac while _narration_out still says .mp3).
                _narr_dur: float = 0.0
                try:
                    from agents.media.audio_engine import (  # noqa: PLC0415
                        _audio_file_duration_s as _audio_dur_s,
                    )
                    _narr_dur = float(_audio_dur_s(Path(_voice_path)))
                except Exception:
                    _narr_dur = 0.0
                if _narr_dur <= 0.05 and _word_timings:
                    _narr_dur = float(_word_timings[-1][2])
                if _narr_dur <= 0.05:
                    try:
                        from moviepy import AudioFileClip as _AFC  # type: ignore[import]
                        with _AFC(str(_narration_out)) as _afc:
                            _narr_dur = float(_afc.duration or 0.0)
                    except Exception:
                        _narr_dur = 0.0

                _stitched_out = _reel_dir / f"{stem}_v{variant + 1:02d}_voice.mp3"
                _voice_path = _stitch_audio_sequential(
                    _voice_path, _cta_path, _stitched_out, silence_s=1.0
                )

                # Append offset CTA timings → full transcription coverage.
                _cta_offset = _narr_dur + 1.0   # 1.0 s silence gap
                if not _cta_word_timings:
                    try:
                        from agents.media.audio_engine import (  # noqa: PLC0415
                            _audio_file_duration_s as _audio_dur_s2,
                            approximate_word_timings as _approx_wt,
                        )
                        _cta_dur_fill = float(_audio_dur_s2(Path(_cta_path)))
                        _cta_word_timings = _approx_wt(_cta_text, _cta_dur_fill)
                    except Exception:
                        _cta_word_timings = []
                _word_timings = _word_timings + [
                    (w, s + _cta_offset, e + _cta_offset)
                    for w, s, e in _cta_word_timings
                ]
                _LOG.info(
                    "Subtitle coverage extended | narr=%.1fs + 1.0s + cta=%.1fs words "
                    "(%d total tokens) | cta_overlay_start=%.1fs",
                    _narr_dur,
                    _cta_word_timings[-1][2] if _cta_word_timings else 0.0,
                    len(_word_timings),
                    _cta_offset,
                )
            elif _voice_path is None:
                _voice_path = None   # silence path stays None; reel renders without audio

            # 3b. Loudnorm on final voice track (no raw gain — prevents distortion)
            if (
                _voice_path is not None
                and page_ctx
                and (page_ctx.page_id or "").lower() == "master_mei"
            ):
                try:
                    _voice_path = apply_voice_loudnorm(_voice_path)
                except Exception as _ln_exc:  # noqa: BLE001
                    _LOG.debug("VO loudnorm skipped: %s", _ln_exc)
        else:
            _skip_why = (
                "ELEVENLABS_API_KEY not set"
                if not app_config.ELEVENLABS_API_KEY
                else "no narration script"
            )
            if (page_ctx.page_id if page_ctx else "").lower() == "ancient_knowledge":
                raise RuntimeError(
                    f"ECONOMIC_REEL | Voiceover skipped — {_skip_why} "
                    "(refusing silent ancient_knowledge compile)."
                )
            _LOG.warning("ECONOMIC_REEL | Voiceover skipped — %s", _skip_why)

        # ── COST TRACKING: audio/TTS ─────────────────────────────────────────
        if cost_tracker is not None and _voiceover_script:
            # Include CTA characters in TTS cost if a separate CTA was generated.
            # Early voice already billed this reel; a second track would double it.
            _already_tts = int(cost_tracker.category_totals().get("tts_chars") or 0)
            if _already_tts <= 0:
                _cta_chars = len(locals().get("_cta_text", ""))
                cost_tracker.track_audio(
                    char_count=len(_voiceover_script) + _cta_chars,
                    sfx=False,
                )

        # -- Three-channel mix: VO + atmosphere SFX loop + music_v2 BGM --------
        # Topology ported from master_mei; enabled via page USE_MUSIC_V2_BED.
        # NO transient impact SFX — atmosphere drone + BGM bed only.
        # The bed is started before image judging. Join it here and skip a
        # second generate when that thread already wrote the files.
        if _early_music_thread is not None:
            _early_music_thread.join()
        _early_mode = str(_early_music_box.get("mode") or "")
        _impact_sfx_path: "Path | None" = None
        _sfx_loop_path: "Path | None" = None
        _target_dur = page_ctx.reel_duration if page_ctx else 105.0
        _is_mei = bool(page_ctx and (page_ctx.page_id or "").lower() == "master_mei")
        _use_music_bed = bool(
            app_config.ELEVENLABS_API_KEY
            and page_ctx
            and getattr(page_ctx, "use_music_v2_bed", False)
        )
        if _use_music_bed:
            _music_min = 40.0
            try:
                _cfg = getattr(page_ctx, "page_cfg", None) or {}
                _music_min = float(_cfg.get("MUSIC_V2_MIN_SECONDS", 40.0) or 40.0)
            except Exception:
                _music_min = 40.0
            _music_style = (
                page_ctx.ambient_music_style if page_ctx else ("warrior" if _is_mei else "mystery")
            )
            _music_dir = (
                page_ctx.music_prompt_directive_path if page_ctx else None
            )
            if _early_mode == "v2":
                _music_bed = _early_music_box.get("bed")
                _sfx_loop_path = _early_music_box.get("sfx")
            else:
                _music_bed, _impact_sfx_path = generate_master_mei_soundscape(
                    _reel_dir,
                    stem=f"{stem}_v{variant + 1:02d}",
                    duration_seconds=max(_music_min, float(_target_dur)),
                    include_impact_sfx=False,  # kill dual/secondary impact overlap
                    topic=str(resolved_subject or ""),
                    directive_path=_music_dir,
                    style_profile=_music_style,
                    channel_name=(page_ctx.page_id or "") if page_ctx else "",
                )
            _impact_sfx_path = None  # hard disable regardless of return
            if _music_bed is not None:
                _ambient_path = _music_bed
                _LOG.info(
                    "%s | BGM music_v2 ready (≥%.0fs) → %s | style=%s",
                    (page_ctx.page_id or "PAGE").upper(),
                    _music_min,
                    Path(_music_bed).name,
                    _music_style,
                )
                if cost_tracker is not None:
                    try:
                        from agents.media.audio_engine import LAST_MUSIC_COMPOSE_MODE
                        _mode = str(LAST_MUSIC_COMPOSE_MODE or "").strip().lower()
                        cost_tracker.track_music(
                            api=_mode not in ("", "local", "ambient", "none"),
                        )
                    except Exception:  # noqa: BLE001
                        cost_tracker.track_music(api=False)
            # Continuous 10s atmosphere tile → looped 100% of video
            _sfx_out = _reel_dir / f"{stem}_v{variant + 1:02d}_atmosphere_sfx.mp3"
            _sfx_prompt = page_ctx.ambient_sfx_prompt if page_ctx else ""
            if _early_mode != "v2":
                _sfx_loop_path = generate_ambient_track(
                    _sfx_out,
                    duration_seconds=10.0,
                    prompt=_sfx_prompt or None,
                )
            if _sfx_loop_path is not None:
                _LOG.info(
                    "%s | Atmosphere SFX 10s → %s (loop 100%%)",
                    (page_ctx.page_id or "PAGE").upper(),
                    Path(_sfx_loop_path).name,
                )
                if (
                    cost_tracker is not None
                    and _early_mode != "v2"
                    and Path(_sfx_loop_path).resolve() == Path(_sfx_out).resolve()
                ):
                    cost_tracker.track_sfx(calls=1)
                elif cost_tracker is not None and _early_mode == "v2":
                    cost_tracker.track_sfx(calls=1)
        elif app_config.ELEVENLABS_API_KEY and _early_mode == "ambient":
            _ambient_path = _early_music_box.get("ambient")
            _ambient_out = _reel_dir / f"{stem}_v{variant + 1:02d}_ambient.mp3"
            if _ambient_path is not None and Path(_ambient_path).is_file():
                _sfx_loop_path = Path(_ambient_path)
                if cost_tracker is not None:
                    cost_tracker.track_sfx(calls=1)
        elif app_config.ELEVENLABS_API_KEY:
            _ambient_out = _reel_dir / f"{stem}_v{variant + 1:02d}_ambient.mp3"
            _sfx_prompt = page_ctx.ambient_sfx_prompt if page_ctx else ""
            _ambient_path = generate_ambient_track(
                _ambient_out,
                duration_seconds=min(12.0, float(_target_dur)),
                prompt=_sfx_prompt or None,
            )
            # Single-tile path: also feed as loopable SFX so mix hears it
            if _ambient_path is not None and Path(_ambient_path).is_file():
                _sfx_loop_path = Path(_ambient_path)
                if (
                    cost_tracker is not None
                    and Path(_ambient_path).resolve() == Path(_ambient_out).resolve()
                ):
                    cost_tracker.track_sfx(calls=1)

        # -- Local ambient pad = FALLBACK only (never replace a generated bed) ─
        # NEVER prefer legacy rain/martial rain loops (hiss/clipping).
        if page_ctx and (_ambient_path is None or not Path(_ambient_path).is_file()):
            _local_ambient = page_ctx.ambient_audio_path
            _ban_rain = ("rain", "martial_loop", "storm", "thunder", "hiss")
            if (
                _local_ambient is not None
                and _local_ambient.is_file()
                and not any(b in _local_ambient.name.lower() for b in _ban_rain)
            ):
                _ambient_path = _local_ambient
                _LOG.info(
                    "%s | Using local ambient fallback: %s",
                    (page_ctx.page_id or "").upper(),
                    _local_ambient.name,
                )
            elif (
                _local_ambient is not None
                and _local_ambient.is_file()
                and any(b in _local_ambient.name.lower() for b in _ban_rain)
            ):
                _LOG.warning(
                    "%s | Ignoring rain/hiss ambient asset %s — using cinematic SFX/drone",
                    (page_ctx.page_id or "").upper(),
                    _local_ambient.name,
                )
            elif (page_ctx.page_id or "").lower() == "ancient_knowledge":
                _legacy_mystery = app_config.ENGINE_ROOT / "assets" / "audio" / "ambient_mystery_loop.mp3"
                if _legacy_mystery.is_file():
                    _ambient_path = _legacy_mystery
                    _LOG.info(
                        "ANCIENT_KNOWLEDGE | Local ambient mystery fallback: %s",
                        _legacy_mystery.name,
                    )

        # Force ambient to 48 kHz (light loudnorm) before MoviePy mix
        if _ambient_path is not None and Path(_ambient_path).is_file():
            try:
                from agents.media.audio_engine import resample_audio_48k as _rs48
                # Copy into reel dir if using a shared asset, so we don't mutate originals
                _amb_src = Path(_ambient_path)
                if _amb_src.resolve().parent != _reel_dir.resolve():
                    _amb_copy = _reel_dir / f"{stem}_v{variant + 1:02d}_ambient_48k.mp3"
                    import shutil as _sh_amb
                    _sh_amb.copy2(_amb_src, _amb_copy)
                    _ambient_path = _rs48(_amb_copy, apply_loudnorm=True)
                else:
                    _ambient_path = _rs48(_amb_src, apply_loudnorm=True)
            except Exception as _amb_rs_exc:  # noqa: BLE001
                _LOG.debug("Ambient 48k resample skipped: %s", _amb_rs_exc)

        # -- Compile reel via moviepy --
        # Build a readable slug from the hook text for the filename.
        _hook_slug = (
            "".join(c if c.isalnum() else "_" for c in overlay_text.lower()).strip("_")[:32]
            if overlay_text else stem
        )
        _reel_target = _reel_dir / f"reel_{_hook_slug}_v{variant + 1:02d}.mp4"
        _reel_dur = max(30.0, page_ctx.reel_duration if page_ctx else 30.0)
        try:
            # Logo PNG — composited as a fully static post-zoom RGBA layer.
            # Use the same logo_path resolved in Phase C (identical to SMART_BAIT path).
            # Fall back to a fresh page_ctx lookup only if Phase C returned None.
            _logo_img_path: Path | None = (
                logo_path                          # Phase C resolution — shared with SMART_BAIT
                or (page_ctx.logo_png if (page_ctx and page_ctx.logo_exists) else None)
            )
            # Brand text label (used only when no logo PNG is found AND logo_image_path is unset)
            _brand_label = (
                f"@ {page_ctx.display_name}"
                if (page_ctx and page_ctx.display_name and not _logo_img_path)
                else None
            )
            # ── DISPATCH: sequence reel (multi-image) vs single-image reel ───
            _use_sequence = (
                page_ctx is not None
                and page_ctx.enable_sequence_reel
                and len(_sequence_image_paths) >= 2
            )
            if _use_sequence:
                _LOG.info(
                    "SEQUENCE_REEL | %d images → %s",
                    len(_sequence_image_paths), _reel_target.name,
                )
                # Page subtitle chrome (size/fill/stroke) applies to every
                # sequence channel. master_mei used to be the only page that
                # received stroke; AK then burned 56px yellow with no outline
                # and looked tiny on Shorts.
                _wpp = page_ctx.subtitle_words_per_phrase if page_ctx else 4
                _sub_fill = page_ctx.subtitle_fill if page_ctx else (255, 230, 0)
                _sub_sw = page_ctx.subtitle_stroke_width if page_ctx else 0
                _sub_sf = page_ctx.subtitle_stroke_fill if page_ctx else None
                # Master Mei: VO +15% (VOICE_VOLUME_GAIN=1.15). Ambient bed 0.38.
                _voice_gain = (
                    float(page_ctx.voice_volume_gain)
                    if page_ctx and page_ctx.voice_volume_gain is not None
                    else (
                        1.15
                        if page_ctx and (page_ctx.page_id or "").lower() == "master_mei"
                        else 1.0
                    )
                )
                _amb_mul = (
                    page_ctx.ambient_sfx_gain_mul
                    if page_ctx and page_ctx.ambient_sfx_gain_mul is not None
                    else 1.0
                )
                _paced_act_durs = None
                if page_ctx and not _is_mei and _sequence_image_paths:
                    _timeline_s = float(_reel_dur or page_ctx.reel_duration or 80.0)
                    if _voice_path is not None:
                        try:
                            from agents.media.audio_engine import (  # noqa: PLC0415
                                _audio_file_duration_s as _voice_dur_s,
                            )
                            _vd = float(_voice_dur_s(Path(_voice_path)))
                            if _vd > 1.0:
                                _timeline_s = _vd + 1.0  # match audio-driven + tail pad
                        except Exception:
                            pass
                    _n_imgs = len(_sequence_image_paths)
                    _plan_ref = locals().get("_planned_scene_durs")
                    if _plan_ref and len(_plan_ref) == _n_imgs:
                        _paced_act_durs = _scale_scene_durations(_plan_ref, _timeline_s)
                        _LOG.info(
                            "ECONOMIC_REEL plan_scenes act_durs | %s → scaled %s",
                            _describe_scene_plan(_plan_ref),
                            _describe_scene_plan(_paced_act_durs),
                        )
                    elif page_ctx.uses_plan_scenes_pacing:
                        # Image count drifted — keep curve shape, pad/trim, then scale.
                        if _plan_ref:
                            _base = list(_plan_ref)
                            if len(_base) > _n_imgs:
                                _base = _base[:_n_imgs]
                            else:
                                _pad = _base[-1] if _base else 5.0
                                _base = _base + [_pad] * (_n_imgs - len(_base))
                            _paced_act_durs = _scale_scene_durations(_base, _timeline_s)
                        else:
                            _paced_act_durs = _plan_scenes(
                                _timeline_s,
                                page_ctx.scene_duration,
                                progressive_start_s=page_ctx.scene_progressive_start_s,
                                progressive_step_every=page_ctx.scene_progressive_step_every,
                                progressive_step_s=page_ctx.scene_progressive_step_s,
                                progressive_cap_s=page_ctx.scene_progressive_cap_s,
                                min_scenes=_n_imgs,
                                max_scenes=_n_imgs,
                            )
                        _LOG.info(
                            "ECONOMIC_REEL plan_scenes (aligned n=%d) | %s",
                            _n_imgs,
                            _describe_scene_plan(_paced_act_durs),
                        )
                    elif page_ctx.reel_use_hook_body_pacing:
                        _paced_act_durs = _build_hook_body_act_durations(
                            _n_imgs,
                            _timeline_s,
                            hook_hold_s=page_ctx.reel_hook_hold_s,
                            body_hold_s=page_ctx.reel_body_hold_s,
                        )
                        _LOG.info(
                            "ECONOMIC_REEL legacy hook/body act_durs | %s",
                            _describe_scene_plan(_paced_act_durs),
                        )
                _compile_act_durs = (
                    list(locals().get("_ak_act_durs") or [])
                    if (
                        page_ctx
                        and str(getattr(page_ctx, "page_id", "")).lower() == "ancient_knowledge"
                        and locals().get("_ak_act_durs")
                    )
                    else _paced_act_durs
                )
                _act_video_paths = None
                if _is_wan_reel:
                    from core.wan_reel_production import (
                        generate_wan_act_videos as _gen_wan_acts,
                    )

                    _wan_dir = (
                        (_episode_assets_dir or Path(app_config.ASSETS_DIR))
                        / "wan_clips"
                    )
                    _holds = [float(d) for d in (_compile_act_durs or [])]
                    _n_imgs = len(_sequence_image_paths)
                    if not _holds:
                        _holds = [float(_reel_dur) / max(1, _n_imgs)] * _n_imgs
                    if len(_holds) > _n_imgs:
                        _holds = _holds[:_n_imgs]
                    elif len(_holds) < _n_imgs:
                        _pad = _holds[-1] if _holds else 5.0
                        _holds = _holds + [_pad] * (_n_imgs - len(_holds))
                    _wan_t0 = time.monotonic()
                    _LOG.info(
                        "WAN_REEL | parallel Wan img2vid | n=%d holds=%s",
                        _n_imgs, ",".join(f"{h:.2f}" for h in _holds),
                    )
                    print(
                        f"\n[WAN_REEL] Generating {_n_imgs} Wan clips in parallel "
                        f"(cap=REMOTE_GPU_MAX_PARALLEL)…"
                    )
                    _act_video_paths, _wan_act_status = _gen_wan_acts(
                        [Path(p) for p in _sequence_image_paths],
                        list(_spoken_snippets or []),
                        _holds,
                        topic=str(resolved_subject or ""),
                        output_dir=_wan_dir,
                        stem=stem,
                        cost_tracker=cost_tracker,
                    )
                    _wan_stage_times["wan_video_s"] = round(
                        time.monotonic() - _wan_t0, 2
                    )
                    _n_wan = sum(1 for s in _wan_act_status if s == "wan")
                    _n_fb = len(_wan_act_status) - _n_wan
                    print(
                        f"[WAN_REEL] Video gen {_wan_stage_times['wan_video_s']:.1f}s | "
                        f"wan={_n_wan} ken_burns_fallback={_n_fb}"
                    )
                    _LOG.info(
                        "WAN_REEL video gen | wall=%.1fs | wan=%d fallback=%d",
                        _wan_stage_times["wan_video_s"], _n_wan, _n_fb,
                    )
                    _compile_t0 = time.monotonic()
                if (
                    page_ctx
                    and (page_ctx.page_id or "").lower() == "ancient_knowledge"
                ):
                    assert_voice_and_subtitles_ready(
                        voice_audio=_voice_path,
                        word_timings=_word_timings,
                        page_id="ancient_knowledge",
                    )
                reel_path = _core_compile_sequence_reel(
                    _sequence_image_paths,
                    overlay_text,
                    voice_audio=_voice_path,
                    ambient_audio=_ambient_path,
                    output_path=_reel_target,
                    target_duration=_reel_dur,
                    act_duration_s=page_ctx.reel_act_duration if page_ctx else None,
                    act_durations=_compile_act_durs,
                    strict_act_durations=(
                        bool(
                            page_ctx
                            and str(getattr(page_ctx, "page_id", "")).lower() == "ancient_knowledge"
                            and locals().get("_ak_act_durs")
                        )
                    ),
                    word_timings=_word_timings or None,
                    font_path=_font_path_abs or None,
                    overlay_opacity=page_ctx.reel_overlay_opacity if page_ctx else 0.35,
                    enable_hook_text=page_ctx.enable_top_hook_text if page_ctx else True,
                    vignette_strength=page_ctx.vignette_strength if page_ctx else 0.0,
                    grain_intensity=page_ctx.grain_intensity if page_ctx else 18.0,
                    logo_image_path=_logo_img_path,
                    logo_width_px=page_ctx.logo_width_px if page_ctx else 200,
                    logo_y_offset_px=page_ctx.logo_y_offset_px if page_ctx else 100,
                    logo_opacity=page_ctx.logo_opacity if page_ctx else 0.85,
                    logo_max_height_px=page_ctx.logo_max_height_px if page_ctx else None,
                    subtitle_fontsize=page_ctx.subtitle_fontsize if page_ctx else 56,
                    subtitle_y_position=page_ctx.subtitle_y_position if page_ctx else None,
                    hook_y_frac=page_ctx.hook_y_frac if page_ctx else 0.50,
                    page_id=page_ctx.page_id if page_ctx else "",
                    words_per_phrase=_wpp,
                    subtitle_fill=_sub_fill,
                    subtitle_stroke_width=_sub_sw,
                    subtitle_stroke_fill=_sub_sf,
                    enable_flicker=page_ctx.enable_flicker if page_ctx else False,
                    enable_light_rays=page_ctx.enable_light_rays if page_ctx else False,
                    enable_dust_particles=page_ctx.enable_dust_particles if page_ctx else False,
                    enable_light_refraction=page_ctx.enable_light_refraction if page_ctx else False,
                    # Guaranteed CTA subtitle overlay — never cut off by act-boundary split
                    # Strip audio behavior tags so [chuckles] etc. never burn onto screen.
                    cta_text=_re.sub(
                        r'\[(?:cackles|chuckles|dry\s*laugh)\]\s*',
                        '',
                        locals().get("_cta_text", "") or "",
                        flags=_re.IGNORECASE,
                    ).strip(),
                    # Outro-only: CTA text starts after narration + 1.0s silence.
                    # Require a real narr duration (>0.5s) — a 0.0 fallback made
                    # the overlay paint from t=1s for the entire reel.
                    cta_start_s=(
                        float(locals().get("_narr_dur", -1.0)) + 0.3
                        if float(locals().get("_narr_dur", -1.0) or -1.0) > 0.5
                        else -1.0
                    ),
                    cta_y_position=(
                        page_ctx.cta_subtitle_y_position if page_ctx else None
                    ),
                    narration_duration_s=(
                        float(locals().get("_narr_dur", -1.0))
                        if float(locals().get("_narr_dur", -1.0) or -1.0) > 0.5
                        else None
                    ),
                    cta_visual_gap_s=0.3,
                    ambient_volume=(page_ctx.ambient_volume if page_ctx else None),
                    sfx_loop_audio=_sfx_loop_path,
                    sfx_loop_volume=(
                        page_ctx.atmosphere_sfx_volume if page_ctx else 0.35
                    ),
                    sfx_fade_in_s=(
                        page_ctx.atmosphere_sfx_fade_in if page_ctx else 0.2
                    ),
                    impact_sfx_audio=None if _use_music_bed else _impact_sfx_path,
                    impact_sfx_volume=(0.0 if _use_music_bed else None),
                    hook_max_s=(
                        float((getattr(page_ctx, "page_cfg", None) or {}).get(
                            "REEL_HOOK_MAX_S", 8.0
                        ))
                        if page_ctx and _is_mei
                        else 0.0
                    ),
                    voice_volume_gain=(1.0 if _is_mei else _voice_gain),
                    ambient_gain_mul=_amb_mul,
                    ambient_duck_ratio=(
                        page_ctx.ambient_duck_ratio if page_ctx else None
                    ),
                    ambient_duck_until_s=locals().get("_narr_dur", None),
                    master_audio_gain=(
                        page_ctx.master_audio_gain
                        if page_ctx and _use_music_bed
                        else None
                    ),
                    ambient_profile=(
                        page_ctx.ambient_music_style
                        if page_ctx
                        else ("warrior" if _is_mei else "mystery")
                    ),
                    bgm_start_s=(page_ctx.bgm_start_time if page_ctx else 0.0),
                    bgm_fade_in_s=(page_ctx.bgm_fade_in_duration if page_ctx else 0.0),
                    sfx_volume_gain_db=(
                        page_ctx.sfx_volume_gain_db if page_ctx else 0.0
                    ),
                    tail_pad_s=page_ctx.reel_tail_pad_s if page_ctx else 1.0,
                    force_exact_duration=(
                        page_ctx.reel_force_exact_duration if page_ctx else False
                    ),
                    exact_duration_s=(
                        page_ctx.reel_duration if page_ctx else 80.0
                    ),
                    ffmpeg_preset=(
                        page_ctx.encoding_preset if page_ctx else "medium"
                    ),
                    pacing_sequence=(
                        page_ctx.pacing_sequence if page_ctx else None
                    ),
                    scene_length=(
                        page_ctx.scene_length if page_ctx else None
                    ),
                    target_duration_min=(
                        page_ctx.reel_duration_target_min if page_ctx else None
                    ),
                    target_duration_max=(
                        page_ctx.reel_duration_target_max if page_ctx else None
                    ),
                    enable_subtitle_padding=(
                        page_ctx.enable_subtitle_padding if page_ctx else True
                    ),
                    act_video_paths=_act_video_paths,
                )
                if _is_wan_reel and "_compile_t0" in locals():
                    _wan_stage_times["compile_s"] = round(
                        time.monotonic() - float(_compile_t0), 2
                    )
            else:
                reel_path = compile_dynamic_reel(
                    Path(img_path_display),
                    overlay_text,
                    voice_audio=_voice_path,
                    ambient_audio=_ambient_path,
                    output_path=_reel_target,
                    target_duration=_reel_dur,
                    font_path=_font_path_abs or None,
                    font_size_scale=_font_size_scale,
                    overlay_opacity=page_ctx.reel_overlay_opacity if page_ctx else 0.35,
                    word_timings=_word_timings or None,
                    brand_label=_brand_label,
                    logo_image_path=_logo_img_path,
                    subtitle_fontsize=page_ctx.subtitle_fontsize if page_ctx else 46,
                    subtitle_y_position=page_ctx.subtitle_y_position if page_ctx else None,
                    logo_width_px=page_ctx.logo_width_px if page_ctx else 160,
                    logo_y_offset_px=page_ctx.logo_y_offset_px if page_ctx else 90,
                    logo_opacity=page_ctx.logo_opacity if page_ctx else 0.70,
                    logo_max_height_px=page_ctx.logo_max_height_px if page_ctx else None,
                    hook_y_frac=page_ctx.hook_y_frac if page_ctx else 0.55,
                    page_id=page_ctx.page_id if page_ctx else "",
                    sub_text=None,
                )
            _LOG.info(
                "%s compiled → %s",
                "WAN_REEL" if _is_wan_reel else "ECONOMIC_REEL",
                reel_path.name,
            )
            video_path_str = str(reel_path)
            print(f"[reel] Video compiled -> {reel_path}")
            try:
                from modules.asset_library import register_asset

                _lib_channel = (
                    page_ctx.page_id if page_ctx else app_config.ACTIVE_PAGE
                ) or "ancient_knowledge"
                _lib_caption = caption if caption and caption != "(skipped)" else (
                    locals().get("_early_seq_script") or overlay_text or ""
                )
                _lib_audio = float(
                    locals().get("_ak_total_audio")
                    or locals().get("_narr_dur")
                    or 0.0
                )
                _lib_meta = locals().get("_sr_act_meta") or {}
                _lib_video = str(reel_path)
                for _si, _still in enumerate(_sequence_image_paths or []):
                    _meta = _lib_meta.get(_si) or {}
                    register_asset(
                        channel=str(_lib_channel),
                        post_type=str(post_type or "ECONOMIC_REEL"),
                        local_path=str(_still),
                        prompt=str(_meta.get("prompt") or ""),
                        caption=str(_lib_caption),
                        video_path=_lib_video if _si == 0 else "",
                        audio_duration_s=_lib_audio if _lib_audio > 0 else None,
                        asset_kind="still",
                        platform="facebook",
                    )
                register_asset(
                    channel=str(_lib_channel),
                    post_type=str(post_type or "ECONOMIC_REEL"),
                    local_path=_lib_video,
                    prompt="",
                    caption=str(_lib_caption),
                    video_path=_lib_video,
                    audio_duration_s=_lib_audio if _lib_audio > 0 else None,
                    asset_kind="video",
                    platform="facebook",
                )
            except Exception as _lib_exc:  # noqa: BLE001
                _LOG.warning("asset_library reel register skipped (%s)", _lib_exc)
            if _is_wan_reel and _wan_stage_times:
                _fb = [s for s in _wan_act_status if s != "wan"]
                print(
                    "[WAN_REEL] Stages | "
                    + " | ".join(f"{k}={v:.1f}s" for k, v in _wan_stage_times.items())
                )
                if _wan_act_status:
                    _n_wan = sum(1 for s in _wan_act_status if s == "wan")
                    print(
                        f"[WAN_REEL] Acts | wan={_n_wan}/{len(_wan_act_status)} "
                        f"ken_burns_fallback={len(_wan_act_status) - _n_wan}"
                    )
                    for _i, _st in enumerate(_wan_act_status, 1):
                        if _st != "wan":
                            print(f"  act {_i}: {_st}")
            _api_imgs = max(0, int(_images_generated_this_variant or 0))
            _batch_so_far = int(_api_imgs)
            _lock = batch_image_lock or write_lock
            if batch_image_counter is not None:
                with _lock:
                    batch_image_counter[0] = int(batch_image_counter[0]) + int(_api_imgs)
                    _batch_so_far = int(batch_image_counter[0])
            _cats = cost_tracker.category_totals() if cost_tracker is not None else {}
            _reel_cost = (
                float(_cats.get("total") or 0.0)
                if _cats
                else (0.003 * _api_imgs)
            )
            _batch_usd = _reel_cost
            if batch_cost_tracker is not None:
                _batch_cats = batch_cost_tracker.category_totals()
                _batch_usd = float(_batch_cats.get("total") or 0.0) + _reel_cost
            _reel_stills = len([
                _still for _still in (_sequence_image_paths or []) if _still
            ])
            _print_video_cost_summary(
                cost_tracker,
                image_count=_api_imgs,
                reel_stills=_reel_stills,
                image_model_label="FLUX Schnell",
                variant_index=variant + 1,
                total_variants=qty,
                batch_images_so_far=_batch_so_far,
                reel_cost_usd=_reel_cost,
                batch_cost_usd=_batch_usd,
                post_type=post_type,
                audio_label=(
                    "F5TTS"
                    if _reel_audio_is_f5()
                    else "ElevenLabs"
                ),
            )

            # ── PHASE E1: Backblaze B2 upload ────────────────────────────────
            # Upload the finished MP4 to B2 and store the public URL so the
            # postplanner MEDIA URL column contains a live, shareable link.
            # On storage-cap AccessDenied: quiet warn → local/ImgBB only.
            _b2_video_url: str = ""
            try:
                _b2 = B2VideoUploader()
                _b2_video_url = _b2.upload(reel_path)
                _LOG.info("B2 upload OK → %s", _b2_video_url)
                print(f"[B2] Public URL: {_b2_video_url}")
            except B2StorageCapError as _b2_cap:
                _LOG.warning("%s", _b2_cap)
                _b2_video_url = ""
            except Exception as _b2_exc:  # noqa: BLE001
                _err_l = str(_b2_exc).lower()
                if "accessdenied" in _err_l or "access denied" in _err_l:
                    _LOG.warning(
                        "[B2] Storage cap / AccessDenied for %s — using local/ImgBB.",
                        reel_path.name,
                    )
                else:
                    _LOG.warning(
                        "B2 upload failed for %s (%s: %s) — postplanner will use local path.",
                        reel_path.name,
                        type(_b2_exc).__name__,
                        _b2_exc,
                    )
                _b2_video_url = ""
        except Exception as reel_exc:  # noqa: BLE001
            _LOG.error(
                "ECONOMIC_REEL video compilation failed (variant %s): %s",
                variant + 1, reel_exc, exc_info=True,
            )
            print(
                f"[reel] COMPILE ERROR (variant {variant + 1}): "
                f"{type(reel_exc).__name__}: {reel_exc}"
            )
            reel_path = None

    if _is_economic_reel:
        from modules.reel_qa.pipeline import seal_compiled_reel

        _qa_beats = list(_beat_qa_rows)
        if not _qa_beats:
            _qa_beats = [
                {
                    "index": 0,
                    "text": str(overlay_text or resolved_subject or ""),
                    "image_path": str(img_path_display or ""),
                    "passed": None,
                }
            ]
        _qa_dir = Path(app_config.PAGE_OUTPUTS_DIR) / "qa"
        _qa_report = seal_compiled_reel(
            video_path=reel_path,
            post_type=post_type,
            beats=_qa_beats,
            word_timings=list(
                locals().get("_word_timings") or locals().get("_narr_word_timings") or []
            ),
            output_dir=_qa_dir,
            stem=f"{stem}_v{variant + 1:02d}",
            channel=(page_ctx.page_id if page_ctx else "") or "",
            blocked_reason="beat_qa_failed" if _reel_qa_block_compile else "",
            cta_text=str((page_ctx.reel_cta_text if page_ctx else "") or ""),
        )

    # ====================================================================
    # PHASE E: ImgBB upload — always uploads the final composited asset
    # ====================================================================
    imgbb_url = ""
    _upload_candidate: Path | str = img_path_display
    if isinstance(_upload_candidate, Path) and _upload_candidate.is_file():
        key_ib = app_config.IMGBB_API_KEY
        if key_ib:
            try:
                imgbb_url = upload_image_file_to_imgbb(key_ib, _upload_candidate) or ""
            except Exception as up_exc:  # noqa: BLE001
                _LOG.warning(
                    "ImgBB upload exception (%s): %s",
                    _upload_candidate.name,
                    up_exc,
                    exc_info=True,
                )
            if not imgbb_url:
                _LOG.warning(
                    "ImgBB upload returned empty URL for %s; planner media column stays blank.",
                    _upload_candidate.name,
                )
        else:
            _LOG.warning("IMGBB_API_KEY missing; CONTENT: MEDIA stays blank.")

    # ---- CAROUSEL: upload every slide so the library holds ALL images -------
    # Slide 1 was uploaded above (imgbb_url); slides 2..N are uploaded here so
    # the library entry registers each frame of the post, not just the first.
    _carousel_imgbb.clear()
    if post_type == "CAROUSEL" and _carousel_image_paths:
        key_ib = app_config.IMGBB_API_KEY
        for _c_idx, _c_path in enumerate(_carousel_image_paths, start=1):
            _c_path_obj = Path(_c_path)
            _c_url = ""
            if imgbb_url and _c_idx == 1 and str(Path(img_path_display).resolve()) == str(_c_path_obj.resolve()):
                _c_url = imgbb_url
            elif key_ib and _c_path_obj.is_file():
                try:
                    _c_url = upload_image_file_to_imgbb(key_ib, _c_path_obj) or ""
                except Exception as _cu:  # noqa: BLE001
                    _LOG.warning("CAROUSEL imgbb slide %d failed: %s", _c_idx, _cu)
            _carousel_imgbb.append({
                "index": _c_idx,
                "imgbb_url": _c_url,
                "path_under_engine": path_under_engine(app_config.ENGINE_ROOT, _c_path_obj),
                "absolute_path": str(_c_path_obj),
            })

    # ====================================================================
    # PHASE E: Durable JSON + planner writes (all post types, unified)
    # ====================================================================

    # ---- SMART_BAIT / ECONOMIC_REEL durable write -----------------------
    if post_type in ("SMART_BAIT", "ECONOMIC_REEL", "WAN_REEL") and not skip_caption:
        if caption_mode_tag != "researcher_fallback" and (overlay_text or caption):
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
            durable_fname = f"post_{stamp}_v{variant + 1:02d}.json"
            durable_abs = app_config.LIBRARY_DIR / durable_fname
            created_iso = datetime.now(timezone.utc).isoformat()
            smart_bait_payload: dict[str, Any] = {
                "page_id": page_ctx.page_id if page_ctx else app_config.ACTIVE_PAGE,
                "avatar_mode": avatar_mode,
                "post_format": post_format,
                "post_type": post_type,
                "topic": resolved_subject,
                "subject_slug": slug,
                "variant_index": variant + 1,
                "quantity_total": qty,
                "economic_brain_mode": economic,
                "image_relative": img_ref_engine,
                "video_path": str(reel_path) if reel_path else video_path_str,
                "b2_url": _b2_video_url,
                "imgbb_url": imgbb_url,
                "overlay_text": overlay_text,
                "humanized_caption": caption,
                "caption_status": caption_mode_tag,
                "created_utc": created_iso,
                "qa_verdict": (_qa_report or {}).get("verdict") or "",
                "qa_report_path": (_qa_report or {}).get("report_path") or "",
                "publishable": bool((_qa_report or {}).get("publishable")),
            }
            if (page_ctx.page_id if page_ctx else "").lower() == "master_mei":
                from agents.media.mei_narrative import (
                    build_seo_description,
                    build_seo_title,
                )
                _llm_seo = getattr(caption_engine, "last_seo_title", "") if caption_engine else ""
                smart_bait_payload["title"] = (
                    (_llm_seo or build_seo_title(resolved_subject)).strip()[:100]
                )
                smart_bait_payload["description"] = build_seo_description(
                    resolved_subject, caption=caption or "",
                )
            write_atomic_json(durable_abs, smart_bait_payload)
            try:
                with write_lock:
                    _planner_type = "VIDEO" if _is_sequence_video_post(post_type) else "IMAGE"
                    if _is_sequence_video_post(post_type) and reel_path:
                        # Prefer B2 public URL; fall back to local path if upload failed.
                        _planner_media = _b2_video_url or str(reel_path)
                    else:
                        _planner_media = imgbb_url
                    from modules.reel_qa.publish import planner_row_allowed

                    if not planner_row_allowed(post_type=post_type, report=_qa_report):
                        _LOG.error(
                            "REEL QA blocked Post Planner | post_type=%s verdict=%s",
                            post_type,
                            (_qa_report or {}).get("verdict"),
                        )
                        print(
                            f"[VisualQA] Post Planner skipped | verdict="
                            f"{(_qa_report or {}).get('verdict') or 'missing'}"
                        )
                    else:
                        planner_row_ix = append_planner_row(
                            app_config.POST_PLANNER_XLSX,
                            posting_time=posting_slot_display,
                            caption=caption,
                            url_link="",
                            media_url=_planner_media,
                            post_type_value=_planner_type,
                            template_path=app_config.BULK_POSTS_TEMPLATE_XLSX,
                        )
            except Exception as fin_exc:  # noqa: BLE001
                _LOG.warning("Smart bait / reel planner write failed (variant %s): %s", variant + 1, fin_exc)

    # ---- STANDARD_QUOTE durable write ------------------------------------
    elif not skip_caption:
        if caption_mode_tag == "researcher_fallback":
            _LOG.warning(
                "All humanizers failed for variant %s of '%s'. "
                "Variant skipped in Excel/CSV; research saved to outputs/logs/.",
                variant + 1,
                resolved_subject,
            )
            merge_update_json(durable_abs, {
                "humanized_caption": "",
                "caption_status": "skipped_humanizer_failure",
                "humanized_utc": datetime.now(timezone.utc).isoformat(),
                "imgbb_url": imgbb_url,
            })
        else:
            if caption_mode_tag == "gemini_fallback":
                _LOG.info("Claude failed; Gemini fallback succeeded for variant %s.", variant + 1)
            caption_payload: dict[str, Any] = {
                "humanized_caption": caption,
                "caption_status": caption_mode_tag,
                "humanized_utc": datetime.now(timezone.utc).isoformat(),
                "imgbb_url": imgbb_url,
            }
            try:
                if durable_abs is not None:
                    merge_update_json(durable_abs, caption_payload)
                else:
                    _LOG.debug(
                        "durable_abs is None (avatar OFF or durable write skipped) — "
                        "skipping merge_update_json for variant %s; Excel write continues.",
                        variant + 1,
                    )
                with write_lock:
                    planner_row_ix = append_planner_row(
                        app_config.POST_PLANNER_XLSX,
                        posting_time=posting_slot_display,
                        caption=caption,
                        url_link="",
                        media_url=imgbb_url,
                        post_type_value="IMAGE",
                        template_path=app_config.BULK_POSTS_TEMPLATE_XLSX,
                    )
            except Exception as fin_exc:  # noqa: BLE001
                _LOG.warning(
                    "Post-humanizer durable/Excel write failed (variant %s): %s",
                    variant + 1,
                    fin_exc,
                    exc_info=True,
                )

    # ---- Skip-caption planner write --------------------------------------
    if skip_caption:
        caption_mode_tag = "skipped"
        with write_lock:
            planner_row_ix = append_planner_row(
                app_config.POST_PLANNER_XLSX,
                posting_time=posting_slot_display,
                caption=caption if isinstance(caption, str) else "(skipped)",
                url_link="",
                media_url=imgbb_url,
                post_type_value="IMAGE",
                template_path=app_config.BULK_POSTS_TEMPLATE_XLSX,
            )

    logging.info("--- VARIANT %s | TOPIC `%s` ---", variant + 1, resolved_subject)
    logging.info("RAW FACT SHEET (researcher)\n%s", raw_sheet)
    logging.info("FINAL CAPTION (humanizer)\n%s", caption)

    if isinstance(raw_sheet, str) and raw_sheet not in ("(skipped)", ""):
        try:
            dump_raw_research_to_log(
                logs_dir,
                run_stamp=run_stamp,
                topic=resolved_subject,
                variant_index=variant + 1,
                raw_fact_sheet=raw_sheet,
            )
        except Exception as log_exc:  # noqa: BLE001
            _LOG.warning("Research log write failed: %s", log_exc)

    caption_str = caption if isinstance(caption, str) else ""
    try:
        from agents.media.free_images.attribution import append_credit, credits_for_images

        _credit_paths = []
        if img_path_display:
            _credit_paths.append(img_path_display)
        _credit_paths.extend(list(locals().get("_sequence_image_paths") or []))
        _image_credit = credits_for_images(_credit_paths)
        if _image_credit:
            caption_str = append_credit(caption_str, _image_credit, limit=2000)
    except Exception:  # noqa: BLE001 — credit is optional metadata
        pass

    meta: dict[str, Any] = {}
    with write_lock:
        meta = append_entry(
            app_config.CONTENT_LIBRARY_PATH,
            build_library_metadata(
                topic=resolved_subject,
                final_caption=caption_str,
                imgbb_url=imgbb_url,
                video_path=video_path_str or "",
                carousel_images=_carousel_imgbb if post_type == "CAROUSEL" else None,
            ),
        )

    from modules.reel_qa.publish import write_postplanner_xlsx_if_allowed

    def _write_timestamped_xlsx() -> None:
        xlsx_path: Path | None = None
        with write_lock:
            xlsx_path = append_postplanner_xlsx_row(
                postplanner_dir,
                run_stamp=run_stamp,
                posting_time=posting_slot_display,
                caption=caption_str,
                media_url=_planner_media_url(
                    post_type,
                    imgbb_url=imgbb_url,
                    local_image=str(img_path_display or ""),
                    b2_video_url=_b2_video_url,
                    video_path=video_path_str,
                ),
            )
        if xlsx_path:
            _LOG.info("PostPlanner XLSX row written: %s", xlsx_path.name)

    try:
        if not write_postplanner_xlsx_if_allowed(
            post_type=post_type,
            report=_qa_report,
            caption_mode=caption_mode_tag,
            write=_write_timestamped_xlsx,
        ) and caption_mode_tag not in ("researcher_fallback", "skipped"):
            _LOG.info(
                "PostPlanner XLSX skipped | QA blocked | verdict=%s",
                (_qa_report or {}).get("verdict"),
            )
    except Exception as xlsx_exc:  # noqa: BLE001
        _LOG.warning("PostPlanner XLSX write failed (variant %s): %s", variant + 1, xlsx_exc)

    if skip_image:
        img_report = "(skipped)"
    elif adapter is not None and adapter.last_gemini_image_model_used:
        img_report = adapter.last_gemini_image_model_used
    else:
        img_report = bm.image_primary_id

    lib_json_rel = path_under_engine(app_config.ENGINE_ROOT, durable_abs) if durable_abs else ""
    _seo_title = ""
    _seo_description = caption_str
    if (page_ctx.page_id if page_ctx else "").lower() == "master_mei":
        from agents.media.mei_narrative import (
            build_seo_description,
            build_seo_title,
        )
        _llm_seo = (
            _seo_title_local
            or (getattr(caption_engine, "last_seo_title", "") if caption_engine else "")
        )
        if _batch_angle and _batch_angle.seo_title_hint and not _llm_seo:
            _seo_title = _batch_angle.seo_title_hint.strip()[:100]
        else:
            _seo_title = (_llm_seo or build_seo_title(resolved_subject)).strip()[:100]
        _seo_description = build_seo_description(
            resolved_subject, caption=caption_str or "",
        )
    elif _batch_angle is not None:
        # Generic pages: unique angle-driven title + description first line
        _seo_title = (
            _seo_title_local
            or (_batch_angle.seo_title_hint or _batch_angle.angle_title)
            or overlay_text
            or resolved_subject
        ).strip()[:100]
        _angle_lead = (
            f"{_batch_angle.angle_title}: {_batch_angle.angle_brief}"
        ).strip()
        if caption_str:
            _seo_description = f"{_angle_lead}\n\n{caption_str}"
        else:
            _seo_description = _angle_lead
    elif _seo_title_local:
        _seo_title = _seo_title_local[:100]

    _return_dict = {
        "topic": resolved_subject,
        "title": _seo_title or overlay_text or resolved_subject,
        "batch_angle": (_batch_angle.angle_title if _batch_angle else ""),
        "hook_style": (_batch_angle.hook_style if _batch_angle else ""),
        "variant_index": variant + 1,
        "local_image_path": img_ref_engine,
        "post_type": post_type,
        "video_path": video_path_str,
        "qa_verdict": (_qa_report or {}).get("verdict") or "",
        "qa_report_path": (_qa_report or {}).get("report_path") or "",
        "imgbb_url": imgbb_url,
        "caption": caption_str,
        "description": _seo_description,
        "overlay_text": overlay_text,
        "library_timestamp": meta.get("timestamp"),
        "library_json_relative": lib_json_rel,
        "excel_row": planner_row_ix,
        "model_image_used": img_report,
        "model_research_head": bm.research_primary_id,
        "humanizer": humanizer_notes,
        "caption_mode": caption_mode_tag if caption_mode_tag is not None else "skipped",
        "pinterest_title": (
            getattr(caption_engine, "last_pinterest_title", "") if caption_engine else ""
        ),
        "pinterest_description": (
            getattr(caption_engine, "last_pinterest_description", "") if caption_engine else ""
        ),
        # Carousel and image-count metadata
        "carousel_image_paths": _carousel_image_paths,
        "carousel_imgbb": _carousel_imgbb,
        "images_generated": _images_generated_this_variant,
        "wan_act_status": list(_wan_act_status or []),
        "wan_stage_times": dict(_wan_stage_times or {}),
        "wan_variant_wall_s": (
            round(time.monotonic() - _wan_t0_variant, 2)
            if post_type == "WAN_REEL"
            else None
        ),
    }

    # ── COST TRACKING: write telemetry + annotate return dict ────────────────
    if cost_tracker is not None and page_ctx is not None and page_ctx.enable_cost_tracking:
        cost_tracker.write_telemetry(
            app_config.LIBRARY_DIR,
            variant_index=variant + 1,
        )
        _pipeline_usd = cost_tracker.pipeline_usd()
        _return_dict["estimated_cost_usd"] = round(_pipeline_usd, 6)
        _return_dict["cost_tier"] = cost_tracker.cost_tier
        _return_dict["cost_breakdown"] = cost_tracker.to_dict()
        _LOG.info(
            "CostTracker | variant %d pipeline=$%.6f ledger=$%.6f tier=%s",
            variant + 1, _pipeline_usd, cost_tracker.total_usd(), cost_tracker.cost_tier,
        )
        if batch_cost_tracker is not None:
            try:
                batch_cost_tracker.merge(cost_tracker)
            except Exception as _mg:  # noqa: BLE001
                _LOG.debug("CostTracker batch merge skipped (%s)", _mg)

    return _return_dict
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
    from agents.orchestrator import PipelineOrchestrator
    from agents.orchestrator.criteria import MAX_RETRIES as _AK_MAX_RETRIES

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
            from modules.asset_library import register_generated_asset

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
        print(f"=== Page: {page_ctx.display_name if page_ctx else 'anna_protocol'} "
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
    # ancient_knowledge is handled above and never reads the PDF/wellness corpus.
    if (
        (not _isolated)
        and _page_id_lower in ("wonder_feed", "master_mei")
        and post_type in ("ECONOMIC_REEL", "SMART_BAIT")
        and page_ctx is not None
        and page_ctx.topic_pool
    ):
        resolved_subject = _rnd.choice(page_ctx.topic_pool)
        if _page_id_lower == "master_mei":
            from agents.media.mei_narrative import episode_theme_meta
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

    from agents.orchestrator import should_use_agentic_pipeline as _should_agentic
    from agents.orchestrator.criteria import MAX_RETRIES as _AK_PRINT_RETRIES

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
        # master_mei: hard-prefer forced avatar.png DNA over cycled avatars
        if (
            page_ctx
            and (page_ctx.page_id or "").lower() == "master_mei"
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
        from modules.durable_store import hydrate_state_file

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


from engine.acts import _build_master_mei_script_act_prompts, _build_reel_act_descriptors, _extract_topic_visual_entities, _is_logo_only_still, _is_sequence_video_post, _load_engagement_bait_examples, _planner_media_url, _silence_noisy_http_loggers, _split_script_into_act_chunks, _strip_audio_behavior_tags  # noqa: E402
from engine.reporting import _bootstrap_pipeline_intro, _print_video_cost_summary, _snapshot_verified_models  # noqa: E402
from engine.stills import _apply_still_cache_to_jobs, _ensure_sequence_image, _export_scene_assets_sequential, _find_cached_sequence_still, _maybe_convert_to_video, _run_acts_parallel, _track_adapter_image, _track_remote_gpu_batch  # noqa: E402
from engine.voice import _commit_reel_angle, _filter_audio_tag_timings, _reel_audio_is_f5, _stitch_audio_sequential, _synthesize_sequence_voice_track, _trim_script_to_word_limit  # noqa: E402
