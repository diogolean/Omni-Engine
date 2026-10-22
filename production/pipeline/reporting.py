# -*- coding: utf-8 -*-
"""reporting steps moved out of main.py. Bodies are unchanged."""
from __future__ import annotations

from production.pipeline.runtime import (
    Any,
    CostTracker,
    PageContext,
    Path,
    PlannedModels,
    _LOGO_ONLY_STILL_POST_TYPES,
    app_config,
    build_model_chain,
    path_under_engine,
    sys,
)
from production.pipeline.log import _LOG
def _color(text: str, code: str) -> str:
    if not getattr(sys.stdout, "isatty", lambda: False)():
        return text
    return f"{code}{text}\033[0m"
def _emit_clean_api_error(exc: BaseException) -> None:
    red = "\033[91m"
    dim = "\033[2m"
    reset = "\033[0m"
    title = type(exc).__name__
    body = getattr(exc, "message", None) or str(exc).strip() or repr(exc)
    print()
    print(_color("API request failed", red))
    print(_color(title, red))
    snippet = body[:2000] + ("\u2026" if len(body) > 2000 else "")
    if not snippet:
        snippet = repr(exc)
    print(dim + snippet + reset)
    print(dim + "Detail: see outputs/logs/run_*.log in this project." + reset)
    print()
def _format_cost_block(
    breakdown: dict,
    post_format: str = "",
    total_images: int = 0,
    scheduled_publish: str = "",
    n_reels: int = 1,
    per_reel_costs: "list[float] | None" = None,
    post_type: str = "",
) -> str:
    """
    Render a formatted terminal cost-analysis block from a CostTracker snapshot.

    Uses official formulas:
      images × $0.003, TTS chars × $0.00003, Gemini tokens × $0.075/1M,
      plus SFX/music API line items. Per-reel rows stay separate from the batch total.
    """
    fallback = "\n| COST ANALYSIS SUMMARY | (unavailable — using $0.00 fallback)\n"
    try:
        if not isinstance(breakdown, dict):
            return fallback
        from core.cost_tracker import CostTracker as _CT
        from core.cost_tracker import image_cost_lines

        tmp = _CT(page_id=str(breakdown.get("page_id") or "summary"))
        cats = tmp.category_totals(breakdown.get("breakdown") or [])
        tier: str = str(breakdown.get("cost_tier") or "—")

        research_cost = float(cats["research_cost"])
        research_tokens = int(cats["research_tokens"])
        image_cost = float(cats["image_cost"])
        image_count = int(cats["image_count"])
        tts_chars = int(cats["tts_chars"])
        audio_cost = float(cats["audio_cost"])
        sfx_calls = int(cats["sfx_calls"])
        music_beds = int(cats["music_beds"])
        music_api = bool(cats["music_api"])

        billed_imgs = image_count
        nano_n = int(cats.get("image_nano_count") or 0)
        real_n = int(cats.get("image_real_count") or 0)
        flux_n = int(cats.get("image_flux_count") or 0)
        reuse_n = int(cats.get("image_reuse_count") or 0)
        split_images = nano_n > 0 or real_n > 0 or reuse_n > 0 or (
            flux_n > 0 and (nano_n or real_n or reuse_n)
        )
        compiled_hint = max(int(total_images or 0), billed_imgs)
        if billed_imgs <= 0 and compiled_hint > 0:
            # Compiled stills with no API hits this run (reused) — keep $0 but label it.
            display_img = compiled_hint
            img_note = "reused stills, no API this run"
        else:
            display_img = billed_imgs
            if image_cost <= 0 and billed_imgs > 0 and not split_images:
                image_cost = billed_imgs * 0.003
            img_note = f"{billed_imgs} generation{'s' if billed_imgs != 1 else ''}"

        n = max(1, int(n_reels or 1))
        _fmt = (post_format or "").upper()
        _pt = (post_type or str(breakdown.get("post_type") or "")).upper().strip()
        from core.cost_tracker import is_static_image_post as _is_static
        from core.cost_tracker import slot_unit_label as _slot_unit

        if _is_static(_pt):
            _is_reel = False
        elif _pt in (
            "ECONOMIC_REEL", "ECONOMIC_REEL_LOFI", "WAN_REEL",
            "REFERENCE_BASED_REELS", "DYNAMIC_REEL",
        ) or _pt.endswith("REEL"):
            _is_reel = True
        else:
            _is_reel = _fmt in ("SEQUENCE_REEL", "DYNAMIC_REEL", "HYBRID_VIDEO")

        if _pt in _LOGO_ONLY_STILL_POST_TYPES:
            _asset_desc = (
                f"{display_img} AI Image{'s' if display_img != 1 else ''} "
                f"({_pt})"
            )
        elif _pt == "ECONOMIC_REEL":
            _asset_desc = (
                f"{display_img} AI Image{'s' if display_img != 1 else ''} "
                f"(ECONOMIC_REEL)"
            )
        elif _is_reel and n > 1:
            _asset_desc = (
                f"{display_img} FLUX API images → {n} MP4 sequence reels"
            )
        elif _is_reel:
            _asset_desc = (
                f"{display_img} FLUX API image{'s' if display_img != 1 else ''}"
                " → 1 MP4 Sequence Reel"
            )
        else:
            _asset_desc = (
                f"{display_img} AI Image{'s' if display_img != 1 else ''} (Static Post)"
            )

        omit_audio = (not _is_reel) and tts_chars <= 0
        if omit_audio:
            audio_cost = 0.0

        _audio_engine = "F5TTS"
        try:
            from core.remote_gpu_manager import is_remote_gpu_enabled as _rgpu_audio

            if not _rgpu_audio("audio"):
                _audio_engine = "ElevenLabs"
        except Exception:
            _audio_engine = "ElevenLabs"

        audio_bits = [f"{tts_chars:,} TTS chars"]
        if sfx_calls:
            audio_bits.append(f"{sfx_calls} SFX")
        if music_beds:
            audio_bits.append(
                f"{music_beds} music {'API' if music_api else 'local $0'}"
            )
        if cats.get("audio_on_plan"):
            audio_bits.append(
                f"plan, ${float(cats.get('informational_usd') or 0.0):.4f} list, not in cap"
            )
        audio_note = ", ".join(audio_bits)

        total = research_cost + image_cost + (0.0 if omit_audio else audio_cost)
        unit, suffix = _slot_unit(_pt if _pt else ("ECONOMIC_REEL" if _is_reel else "LONG_CAPTION_IMAGE"))
        sep = "+" + "=" * 62 + "+"
        thin = "  " + "-" * 60
        lines = [
            "",
            sep,
            f"| {'COST ANALYSIS SUMMARY':<60} |",
            f"| {'Tier: ' + tier:<60} |",
            sep,
            f"  {'Visual Assets:'.ljust(36)} {_asset_desc}",
        ]
        if _pt == "ECONOMIC_REEL":
            lines.append(
                f"  {'Audio Synthesis:'.ljust(36)} "
                f"{tts_chars:,} chars ({_audio_engine})"
            )
        lines += [
            "",
            f"  - Research & Script ({cats.get('text_label') or 'Gemini 2.5 Flash'}):".ljust(42)
            + f"${research_cost:.4f}  ({research_tokens:,} tokens)",
        ]
        if split_images:
            lines.extend(image_cost_lines(cats))
        else:
            lines.append(
                "  - Image Gen (FLUX Schnell):".ljust(42)
                + f"${image_cost:.4f}  ({img_note})"
            )
        if not omit_audio:
            _voice_label = (
                f"  - Voice ({_audio_engine}):"
                if _pt == "ECONOMIC_REEL"
                else "  - Voice & Audio (ElevenLabs):"
            )
            lines.append(
                _voice_label.ljust(42)
                + f"${audio_cost:.4f}  ({audio_note})"
            )
        if _is_reel:
            lines.append(
                "  - Video Render (MoviePy/FFmpeg):".ljust(42)
                + "$0.0000  (local, no API charge)"
            )
        if scheduled_publish:
            lines.append(
                "  - Scheduled Publish (YouTube):".ljust(42)
                + f"{scheduled_publish} (Shorts)"
            )
        if per_reel_costs and (n > 1 or not _is_reel or _pt == "ECONOMIC_REEL"):
            lines.append(thin)
            for i, rc in enumerate(per_reel_costs, 1):
                lines.append(
                    f"  {unit} {i}/{n}{suffix}:".ljust(42) + f"${float(rc):.4f} USD"
                )
        _batch_note = ""
        if n > 1:
            _batch_note = f"  ({n} {unit.lower()}s)"
        lines += [
            thin,
            f"  {'TOTAL ESTIMATED COST:'.ljust(36)} ${total:.4f}" + _batch_note,
            sep,
            "",
        ]
        return "\n".join(lines)
    except Exception:
        return fallback
def _print_production_summary(
    envelope: dict[str, Any],
    page_ctx: PageContext | None = None,
) -> None:
    """End-of-run: image paths and captions surfaced for scheduling / paste."""
    green = "\033[92m"
    cyan = "\033[96m"
    yellow = "\033[93m"
    sep = "+" + "=" * 62 + "+"
    topic_display = envelope.get("resolved_subject") or "(subject)"
    rows = envelope.get("items") or []
    if not isinstance(rows, list):
        rows = []

    print()
    print(_color(sep, green))
    if page_ctx:
        print(_color(f"| PAGE: {page_ctx.display_name:<55}|", cyan))
        print(_color(
            f"| AVATAR: {page_ctx.avatar_mode:<8} FORMAT: {page_ctx.post_format:<43}|", cyan
        ))
    print(_color("| PRODUCTION SUMMARY                                            |", cyan))
    print(_color(sep, green))
    print(_color("Topic:", green), topic_display)
    print()

    if not rows:
        print(_color("(no artifact rows emitted)", cyan))
        print(_color(sep, green))
        print()
        return

    for row in rows:
        cap = row.get("caption") or ""
        img = row.get("local_image_path") or row.get("image_path") or ""
        bb = row.get("imgbb_url") or ""
        mode = row.get("caption_mode", "humanized")
        video = row.get("video_path") or ""
        print(_color(sep, green))
        print(_color(f"Variant {row.get('variant_index', '?')}", cyan))
        if mode == "researcher_fallback":
            print(_color("  Note: Caption is raw researcher output (humanizer failed).", yellow))
        print(_color("  Image path:", green), img or "(skipped)")
        carousel_slides = row.get("carousel_image_paths") or []
        if carousel_slides:
            for _sidx, _spath in enumerate(carousel_slides, 1):
                print(_color(f"  Slide {_sidx:02d}:", green), Path(_spath).name if _spath else "(missing)")
        if video:
            print(_color("  Video path:", green), video)
        if bb:
            print(_color("  ImgBB URL:", green), bb)
        print(_color("  Caption:", green))
        for line in str(cap).splitlines() or ["(empty)"]:
            print(" ", line)
    print(_color(sep, green))
    xlsx_rel = path_under_engine(app_config.ENGINE_ROOT, app_config.POST_PLANNER_XLSX)
    lib_hint = ""
    first = rows[0] if rows else {}
    if isinstance(first, dict) and first.get("library_json_relative"):
        lib_hint = str(first["library_json_relative"])
    print(_color("Records:", green), f"bulk workbook `{xlsx_rel}`" + (f"; library `{lib_hint}`" if lib_hint else ""))

    # Metadata library — always announce its location so the user knows where
    # all generated titles, descriptions, and video paths are persisted.
    _lib_path = app_config.CONTENT_LIBRARY_PATH
    _lib_rel = path_under_engine(app_config.ENGINE_ROOT, _lib_path)
    print(_color("Metadata library:", cyan), str(_lib_path.resolve()))
    print(_color("  (relative):", cyan), _lib_rel)

    # ── COST ANALYSIS ──────────────────────────────────────────────────────
    # Prefer envelope-level final snapshot (has full multi-variant totals).
    # Fall back to first row's per-variant breakdown for single-variant runs.
    _cost_snap = (
        envelope.get("final_cost_breakdown")
        or (rows[0] if rows else {}).get("cost_breakdown")
    )
    _total_imgs = envelope.get("total_images_generated") or 0
    # Collect the earliest scheduled publish time from the uploaded rows (if any).
    _sched_times = [r["youtube_scheduled_at"] for r in rows if r.get("youtube_scheduled_at")]
    _sched_display = _sched_times[0] if _sched_times else ""
    if _cost_snap:
        _per_reel: list[float] = []
        for _row in rows:
            try:
                _per_reel.append(float(_row.get("estimated_cost_usd") or 0.0))
            except (TypeError, ValueError):
                _per_reel.append(0.0)
        print(_format_cost_block(
            _cost_snap,
            post_format=page_ctx.post_format if page_ctx else "",
            total_images=_total_imgs,
            scheduled_publish=_sched_display,
            n_reels=max(1, len(rows)),
            per_reel_costs=_per_reel,
            post_type=str(envelope.get("post_type") or ""),
        ))
    print()
def _snapshot_verified_models(
    *,
    economic_brain_mode: bool,
    page_ctx: PageContext | None = None,
) -> PlannedModels:
    """Determine first-hop model IDs via cost-first Model Router."""
    from agents.media.providers.model_router import image_model, text_model
    from agents.media.providers.together_image import FLUX_SCHNELL_MODEL

    gem_key = app_config.GEMINI_API_KEY
    text_route = text_model(
        task="caption" if economic_brain_mode else "research",
        log=True,
    )
    img_override = None
    if page_ctx is not None:
        img_override = page_ctx.image_model_override
    img_route = image_model(
        task="image",
        model_override=img_override,
        log=True,
    )
    _ak_page = bool(
        page_ctx and (page_ctx.page_id or "").strip().lower() == "ancient_knowledge"
    )
    if _ak_page:
        humanizer = "Ancient Knowledge narration (live text provider)"
    else:
        if economic_brain_mode or not app_config.claude_opted_in():
            humanizer = (
                f"Gemini `{text_route.model_id}` (captions + research) [{text_route.tier}]"
            )
        else:
            humanizer = (
                f"Anthropic Claude `{app_config.resolve_claude_model()}` / "
                f"Gemini `{text_route.model_id}` [{text_route.tier}]"
            )
    if img_override and app_config.is_gemini_image_model(img_override):
        img_pref = app_config.normalize_image_model_id(img_override)
    else:
        img_pref = img_route.model_id or FLUX_SCHNELL_MODEL
    research_pref = text_route.model_id
    if not gem_key:
        return PlannedModels(
            image_primary_id=img_pref,
            research_primary_id=research_pref,
            humanizer_summary=humanizer,
        )

    from agents.media.providers.gemini_utils import make_gemini_client_with_fallback

    client = make_gemini_client_with_fallback(gem_key)
    research_chain = build_model_chain(
        client, capability_type="text", preferred=research_pref
    )
    if text_route.tier != "premium":
        research_chain = [
            m for m in research_chain if "pro" not in m.lower() or "flash" in m.lower()
        ] or [research_pref]
    if app_config.is_gemini_image_model(img_pref):
        _LOG.info(
            "Image backend locked | Gemini `%s` | router_tier=%s",
            img_pref, img_route.tier,
        )
    else:
        _LOG.info(
            "Image backend locked | Together AI `%s` (steps=4, ≈$0.003/img) | router_tier=%s",
            img_pref, img_route.tier,
        )
    return PlannedModels(
        image_primary_id=img_pref,
        research_primary_id=(research_chain[0] if research_chain else research_pref),
        humanizer_summary=humanizer,
    )
def _preflight_gemini_image_batch_cost(*, page_ctx: PageContext | None, quantity: int) -> None:
    """Log estimated Gemini image spend before any API call is made."""
    override = ""
    if page_ctx is not None:
        override = str(
            page_ctx.page_cfg.get("IMAGE_MODEL_OVERRIDE")
            or page_ctx.image_model_override
            or ""
        ).strip()
    if not override or not app_config.is_gemini_image_model(override):
        return
    n = max(1, int(quantity or 1))
    size = str(getattr(app_config, "GEMINI_IMAGE_SIZE", "1K") or "1K")
    unit = float(app_config.estimate_gemini_image_usd(override, image_size=size))
    total = unit * n
    tier = "gemini-pro" if "pro" in override.lower() else "flash"
    print(
        f"[bootstrap] Pre-flight image cost | model={override} | tier={tier} | "
        f"size={size} | {n} img × ${unit:.3f} = ~${total:.2f} USD"
        + (
            "  (flash ~$0.10 / 20)"
            if tier == "flash"
            else "  (gemini-pro 1K ~$0.60 / 20)"
        )
    )
def _bootstrap_pipeline_intro(
    *,
    economic_brain_mode: bool,
    verified: PlannedModels,
    compact: bool = False,
    page_ctx: PageContext | None = None,
) -> None:
    """Confirm persona, credentials, likeness, routing, and verified model IDs."""
    app_config.print_dotenv_bootstrap()

    if page_ctx:
        print(f"[bootstrap] Active page:    {page_ctx.display_name} ({page_ctx.page_id})")
        print(f"[bootstrap] Avatar mode:    {page_ctx.avatar_mode}")
        print(f"[bootstrap] Post format:    {page_ctx.post_format}")

    gem_ok = bool(app_config.GEMINI_API_KEY and str(app_config.GEMINI_API_KEY).strip())
    claude_ok = bool(app_config.ANTHROPIC_API_KEY and str(app_config.ANTHROPIC_API_KEY).strip())

    print(f"[bootstrap] Gemini API Key detected: {'Yes' if gem_ok else 'No'}")
    print(f"[bootstrap] Claude API Key detected: {'Yes' if claude_ok else 'No'}")
    _img_shown = verified.image_primary_id
    if page_ctx and page_ctx.image_model_override:
        _img_shown = page_ctx.image_model_override
    _img_kind = (
        "gemini-native"
        if app_config.is_gemini_image_model(_img_shown)
        else "together-flux"
    )
    print(
        "[bootstrap] Image pipeline:",
        _img_shown,
        f"({_img_kind})",
        "(aspect",
        f"{(page_ctx.image_aspect_ratio if page_ctx and page_ctx.image_aspect_ratio else app_config.GEMINI_IMAGE_ASPECT_RATIO)})",
    )
    if economic_brain_mode:
        print(
            "[bootstrap] Economic brain Gemini preference (research + humanizer):",
            app_config.GEMINI_ECONOMIC_BRAIN_MODEL,
            "(fallback chain rotates on 404; see GEMINI_ALERT log lines)",
        )
    elif app_config.claude_opted_in():
        print(
            "[bootstrap] Premium relay | Gemini researcher preference:",
            app_config.GEMINI_RESEARCH_MODEL,
            "| Claude humanizer:",
            app_config.resolve_claude_model(),
        )
    else:
        print(
            "[bootstrap] Text stack: DeepSeek on DeepInfra, OpenRouter, Gemini.",
            "Claude is off.",
            "Research preference:",
            app_config.GEMINI_RESEARCH_MODEL,
        )

    print(f"[bootstrap] Verified Image Model: {verified.image_primary_id}")
    print(f"[bootstrap] Verified Research Model: {verified.research_primary_id}")
    print(f"[bootstrap] Economic brain mode = {economic_brain_mode}")

    if compact:
        avatar_on = page_ctx.avatar_on if page_ctx else True
        if avatar_on and not app_config.reference_avatar_exists():
            print(
                f"[bootstrap] Warning: reference avatar missing at "
                f"{app_config.reference_avatar_resolved_path()} (text-only likeness).",
            )
        return

    dn_path = app_config.PERSONA_DNA_PATH.resolve()
    print(f"[bootstrap] Persona DNA file in use: {dn_path}")
    print(f"[bootstrap] File present on disk: {dn_path.is_file()}")

    avatar_on = page_ctx.avatar_on if page_ctx else True
    if avatar_on:
        canonical = app_config.reference_avatar_resolved_path()
        print("[bootstrap] Likeness reference path:", canonical)
        print(f"[bootstrap] Reference avatar exists: {app_config.reference_avatar_exists()}")
        app_config.warn_if_reference_avatar_missing()
    else:
        print("[bootstrap] Avatar mode OFF — skipping likeness reference; atmospheric imagery only.")
def _print_video_cost_summary(
    cost_tracker: "CostTracker | None",
    *,
    image_count: int,
    image_model_label: str = "FLUX Schnell",
    variant_index: int = 1,
    total_variants: int = 1,
    batch_images_so_far: int | None = None,
    reel_cost_usd: float | None = None,
    batch_cost_usd: float | None = None,
    post_type: str = "ECONOMIC_REEL",
    audio_label: str = "",
    reel_stills: int | None = None,
) -> None:
    """Print this-reel cost, then running batch total (never mix the two)."""
    from core.cost_tracker import image_cost_lines, print_cost_summary

    cats = cost_tracker.category_totals() if cost_tracker is not None else {}
    img_cost = float(cats.get("image_cost") or 0.0)
    if img_cost <= 0 and image_count > 0 and not image_cost_lines(cats):
        img_cost = 0.003 * max(0, image_count)
    reel_total = (
        float(reel_cost_usd)
        if reel_cost_usd is not None
        else float(cats.get("total") or img_cost)
    )
    _batch_imgs = (
        int(batch_images_so_far)
        if batch_images_so_far is not None
        else int(image_count)
    )
    print_cost_summary(
        variant_index=int(variant_index),
        total_variants=int(total_variants),
        images_this_reel=int(image_count),
        total_batch_images=_batch_imgs,
        total_reel_cost=float(reel_total),
        batch_cost=batch_cost_usd,
        research_cost=float(cats.get("research_cost") or 0.0),
        image_cost=img_cost,
        audio_cost=float(cats.get("audio_cost") or 0.0),
        post_type=str(post_type or "ECONOMIC_REEL"),
        tts_chars=int(cats.get("tts_chars") or 0),
        audio_label=str(audio_label or ""),
        categories=cats,
        reel_stills=reel_stills,
    )
    # The compact "FLUX Schnell N generations" line counted every compiled
    # still, including reused frames, and contradicted the provider split.
    del image_model_label
def _print_test_footer() -> None:
    print("\n--- Test summary ---\n")
    print("Dry-run complete; no Gemini or Claude paid calls were exercised for generation.\n")


