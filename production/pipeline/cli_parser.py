# -*- coding: utf-8 -*-
"""CLI flags. Destinations and defaults are unchanged."""
from __future__ import annotations


from production.pipeline.runtime import (
    Any,
    Path,
    ChannelFactory,
    PlannedModels,
    VALID_AVATAR_MODES,
    VALID_FORMATS,
    VALID_PAGES,
    YouTubeQuotaExceededError,
    _YT_MAX_DAILY_UPLOADS,
    _YtDailyUploadSafetyGate,
    _yt_build_client,
    _yt_build_credentials,
    _yt_queue_pending_upload,
    _yt_resume_pending_queue,
    _yt_token_path,
    _yt_upload_short,
    _yt_verify_channel,
    activate_run_ledger,
    app_config,
    argparse,
    bind_run_channel,
    configure_file_logging,
    datetime,
    ledger_file_path,
    load_page_context,
    logging,
    os,
    random,
    resolve_default_avatar_mode,
    sys,
    resolve_default_format,
    timedelta,
    timezone,
)

def _together_cli_image_model(args: argparse.Namespace) -> tuple[str, str] | None:
    """Return (flag_label, together_model_id) for Together image CLI flags."""
    from agents.media.providers.together_image import (
        FLUX_2_DEV_MODEL,
        JUGGERNAUT_LIGHTNING_FLUX_MODEL,
    )

    if getattr(args, "together_flux2dev", False):
        return "together_flux2dev", FLUX_2_DEV_MODEL
    if getattr(args, "together_juggernaut", False):
        return "together_Juggernaut", JUGGERNAUT_LIGHTNING_FLUX_MODEL
    return None

def parse_cli_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Unified Multi-Page Factory — holistic persona content engine.",
    )
    parser.add_argument(
        "topic",
        nargs="?",
        help='Optional topic/subject ("Castor Oil"). Omit for AI-chosen subjects.',
    )
    parser.add_argument(
        "--ignore-reel-checkpoints",
        action="store_true",
        help=(
            "Do not resume approved reel stills or TTS from the checkpoint. "
            "Same as REEL_CHECKPOINT=ignore."
        ),
    )
    parser.add_argument(
        "--clear-reel-checkpoints",
        action="store_true",
        help=(
            "Delete the reel checkpoint manifest before this run. "
            "Same as REEL_CHECKPOINT=clear."
        ),
    )
    parser.add_argument(
        "--hook-parallax",
        action="store_true",
        help=(
            "Render the first scene as a 2.5D depth-parallax clip instead of "
            "Ken Burns. Needs torch (CPU) and transformers; falls back to Ken "
            "Burns without them. Same as REEL_HOOK_PARALLAX=1."
        ),
    )
    parser.add_argument(
        "--quantity", "--count", "-n",
        dest="quantity",
        type=int,
        default=1,
        help="Number of unique post variants to produce concurrently. Alias: --count, -n. Default: 1.",
    )
    parser.add_argument(
        "--carousel_quantity",
        dest="carousel_quantity",
        type=int,
        default=3,
        metavar="N",
        help=(
            "CAROUSEL post type only: number of images each carousel hosts. "
            "Default: 3. --quantity controls how many carousels are produced."
        ),
    )
    parser.add_argument(
        "--multi-variant",
        dest="multi_variant",
        action="store_true",
        default=False,
        help=(
            "Deconstruct ONE core topic into N sub-angles (legacy BatchPlanner "
            "matrix). Default OFF: --quantity N pulls N completely distinct "
            "subjects from the channel topic pool / content_library lookback."
        ),
    )
    parser.add_argument(
        "--text-overlay", "--gframe",
        dest="text_overlay",
        default=None,
        choices=["ON", "OFF"],
        metavar="ON|OFF",
        help=(
            "Burn headline / G-frame text onto the image canvas. "
            "Default OFF for CAROUSEL, LONG_CAPTION_IMAGE, and CTA_CAPTION_IMAGE "
            "(clean photographs, no slide labels or dark-gradient burn-ins). "
            "SMART_BAIT / IMAGE_QUOTE still overlay text unless this is OFF. "
            "Alias: --gframe."
        ),
    )
    parser.add_argument(
        "--channel", "--page",
        dest="page",
        default="anna_protocol",
        choices=list(VALID_PAGES),
        metavar="CHANNEL",
        help=(
            f"Target channel (page persona). Options: {', '.join(VALID_PAGES)}. "
            "Default: anna_protocol."
        ),
    )
    parser.add_argument(
        "--avatar",
        default=None,
        choices=list(VALID_AVATAR_MODES),
        metavar="AVATAR",
        help=(
            "ON: include human subject + reference likeness in image generation. "
            "OFF: bypass avatar pipeline — generates purely atmospheric background imagery. "
            "Default: derived from page config (anna_protocol=ON, wonder_feed=OFF, down_dirty=OFF)."
        ),
    )
    parser.add_argument(
        "--format",
        dest="post_format",
        default=None,
        choices=list(VALID_FORMATS),
        metavar="FORMAT",
        help=(
            "Output format. "
            "IMAGE_AVATAR: standard portrait (default). "
            "IMAGE_QUOTE: Gemini image + text overlay (legacy alias). "
            "IMAGE_BACKGROUND: hyper-literal Gemini background + text overlay (SMART_BAIT default). "
            "TEXT_QUOTE: brand-colour solid backdrop + text only (zero Gemini image cost). "
            "HYBRID_VIDEO: 7-second Ken Burns zoom loop from generated image."
        ),
    )
    parser.add_argument(
        "--skip-image",
        action="store_true",
        help="Caption + planner only.",
    )
    parser.add_argument(
        "--skip-caption",
        action="store_true",
        help="Image synthesis only.",
    )
    parser.add_argument(
        "--agentic-pipeline",
        dest="agentic_pipeline",
        action="store_true",
        default=None,
        help=(
            "Force the 4-tier agentic pipeline "
            "(ThemeCurator → Copywriter → VisualDirector ⇄ VisualQA). "
            "Auto-on for ancient_knowledge static image posts."
        ),
    )
    parser.add_argument(
        "--no-agentic-pipeline",
        dest="agentic_pipeline",
        action="store_false",
        help="Disable the agentic pipeline and use the legacy per-variant worker path.",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Dry-run: print scaffold prompts/inventory without calling Gemini or Anthropic APIs.",
    )
    parser.add_argument(
        "--debug",
        dest="debug",
        action="store_true",
        default=False,
        help="Verbose diagnostics: keep non-actionable WARNING traces (RAG gaps, VLM fallbacks, TTS wire).",
    )
    parser.add_argument(
        "--cta",
        dest="cta",
        default="ON",
        choices=["ON", "OFF"],
        metavar="CTA",
        help=(
            "ON (default): inject CTA keyword into captions. "
            "OFF: suppress all comment-to-receive CTAs and DM links."
        ),
    )
    parser.add_argument(
        "--post-type",
        dest="post_type",
        default="STANDARD_QUOTE",
        choices=[
            "STANDARD_QUOTE",
            "SMART_BAIT",
            "LONG_CAPTION_IMAGE",
            "CTA_CAPTION_IMAGE",
            "ECONOMIC_REEL",
            "ECONOMIC_REEL_LOFI",
            "WAN_REEL",
            "CAROUSEL",
            "REFERENCE_BASED_REELS",
        ],
        metavar="POST_TYPE",
        help=(
            "STANDARD_QUOTE (default): long-form educational caption from PDF research. "
            "SMART_BAIT: 4-layer image stack (bg + 20%% mask + bold text + logo) with ultra-short "
            "viral hook + sarcastic one-liner caption. Uses illustration_style for Gemini prompt. "
            "LONG_CAPTION_IMAGE: contextual illustration image with ONLY a logo overlay (no text/mask). "
            "Deep long-form storytelling caption about relationships/character. "
            "CTA_CAPTION_IMAGE: anna_protocol only. Same clean logo-only still as "
            "LONG_CAPTION_IMAGE, but the original Holistic Legacy caption: 60%% one "
            "paragraph (350-450 chars) / 40%% 3-4 short paragraphs (550-750 chars), "
            "ending with Comment KEYWORD (GINGER, SALT, DETOX, …). No copyright signature. "
            "ECONOMIC_REEL: graphite image base (same pipeline as SMART_BAIT) compiled into a "
            "vertical 9:16 MP4 reel with ElevenLabs TTS voiceover, dark ambient soundscape, "
            "and cinematic Ken Burns zoom-in. Outputs .mp4 + durable JSON. "
            "ECONOMIC_REEL_LOFI: separate LOFI pipeline (Flux Schnell, no LoRA) — multi-scene "
            "ink/graphic-novel stills, duotone grade, Ken Burns, channel watermark. "
            "Uses --duration (15–90, default 27) and --module (relationship|parenting). "
            "Does NOT share state with ECONOMIC_REEL. "
            "WAN_REEL: ancient_knowledge only. Reuses ECONOMIC_REEL script/TTS/"
            "still pipeline, then Wan2.2 img2vid per act (bucket holds) with "
            "Ken Burns fallback. Smoke test: core.wan_reel_engine.run_wan_reel_test. "
            "CAROUSEL: generates visually cohesive images (slide_01..N) with distinct "
            "scene viewpoints for a multi-part visual narrative post. "
            "Images per carousel = --carousel_quantity (default 3); POSTS = --quantity."
            "REFERENCE_BASED_REELS: extract a raw-footage clip, overlay an LLM hook text, "
            "blend lullaby ambient audio — no image generation. Designed for momma_circle."
        ),
    )
    parser.add_argument(
        "--duration",
        dest="duration",
        type=int,
        default=None,
        metavar="SECONDS",
        help=(
            "ECONOMIC_REEL_LOFI: default 27s / 9 beats (thematic_arc). "
            "Larger --duration adds beats at 3s each (max 90s). "
            "VO is never trimmed; a long line may extend its own slot slightly."
        ),
    )
    parser.add_argument(
        "--module",
        dest="module",
        default=None,
        choices=["relationship", "parenting"],
        metavar="MODULE",
        help=(
            "ECONOMIC_REEL_LOFI only: RAG theme namespace. "
            "relationship (default) or parenting for wonder_feed + momma_circle."
        ),
    )
    parser.add_argument(
        "--lofi-theme",
        dest="lofi_theme",
        default=None,
        metavar="THEME",
        help="ECONOMIC_REEL_LOFI only: force a theme id (e.g. betrayal, loneliness).",
    )
    parser.add_argument(
        "--lofi-subtheme",
        dest="lofi_subtheme",
        default=None,
        metavar="SUBTHEME",
        help="ECONOMIC_REEL_LOFI only: force a subtheme id when using --lofi-theme.",
    )
    parser.add_argument(
        "--lofi-mode",
        dest="lofi_mode",
        choices=["emotional", "theme", "quote", "paraphrase"],
        default="emotional",
        help=(
            "ECONOMIC_REEL_LOFI writer mode (default: emotional). "
            "emotional creates original micro-philosophical storytelling; "
            "theme explicitly selects the slower current composer; "
            "quote develops --lofi-seed-quote as a parable; paraphrase lightly "
            "rewords one aphorism-bank entry."
        ),
    )
    parser.add_argument(
        "--lofi-seed-quote",
        dest="lofi_seed_quote",
        default=None,
        metavar="TEXT",
        help="ECONOMIC_REEL_LOFI --lofi-mode quote: concept to develop.",
    )
    parser.add_argument(
        "--lofi-image-provider",
        dest="lofi_image_provider",
        choices=["together", "gemini"],
        default="together",
        help=(
            "ECONOMIC_REEL_LOFI image provider (default: together). "
            "gemini uses the Google Gemini Flash Image fallback chain."
        ),
    )
    parser.add_argument(
        "--lofi-aphorism-id",
        dest="lofi_aphorism_id",
        default=None,
        metavar="ID",
        help=(
            "ECONOMIC_REEL_LOFI --lofi-mode paraphrase: entry id from "
            "production/formats/lofi/store/aphorism_bank.json. "
            "Omit to pick a random unused bank entry each run."
        ),
    )
    parser.add_argument(
        "--lofi-script",
        dest="lofi_scripts",
        action="append",
        default=None,
        metavar="JSON",
        help=(
            "ECONOMIC_REEL_LOFI: render a locked script JSON (skip writer). "
            "Repeat the flag for multiple locked scripts."
        ),
    )
    parser.add_argument(
        "--lofi-allow-mixed-era",
        dest="lofi_allow_mixed_era",
        action="store_true",
        default=False,
        help=(
            "ECONOMIC_REEL_LOFI: allow assembling stills from different "
            "style tags / pipeline runs. Off by default — mixed-era "
            "videos are refused."
        ),
    )
    parser.add_argument(
        "--stills-only",
        dest="stills_only",
        action="store_true",
        default=False,
        help=(
            "ECONOMIC_REEL_LOFI only: writer + validator + 9 stills + VisualQA, "
            "then stop (no TTS, no MoviePy, no captions). Cheap episode preview."
        ),
    )
    parser.add_argument(
        "--script-only",
        dest="script_only",
        action="store_true",
        default=False,
        help=(
            "ECONOMIC_REEL_LOFI only: run RAG writer + validator + reference "
            "similarity check, print the full script and per-beat table, then "
            "stop before image generation / voiceover / render."
        ),
    )
    parser.add_argument(
        "--dry-run",
        "--json-only",
        dest="dry_run",
        action="store_true",
        default=False,
        help=(
            "ECONOMIC_REEL_LOFI only: JSON-only staging. Runs the real writer + "
            "validator + Stage 3 visual prompt assembly, then writes the FULL "
            "final intermediate JSON (script text, per-beat timings + assembled "
            "visual prompts, and layout config) to outputs/<page>/clips/"
            "lofi_dry_run_*.json and STOPS — no image generation, no TTS, no "
            "MoviePy/FFmpeg render. Review the JSON, then re-run WITHOUT this "
            "flag for the single render test, or add --quantity N for the bulk "
            "batch. Aliases: --json-only."
        ),
    )
    parser.add_argument(
        "--lofi-no-review",
        dest="lofi_no_review",
        action="store_true",
        default=False,
        help=(
            "ECONOMIC_REEL_LOFI: auto-pass Gate 1 (script) only. "
            "Gate 2 (assembled prompts after Stage 3, before any image API) "
            "still requires --lofi-approve-gate 2. Default is "
            "review_required=true: Gate 1 holds before TTS; Gate 2 holds "
            "before image generation."
        ),
    )
    parser.add_argument(
        "--lofi-resume-from",
        dest="lofi_resume_from",
        default=None,
        metavar="JSON",
        help=(
            "ECONOMIC_REEL_LOFI: resume a held four-stage pipeline JSON "
            "(lofi_pipeline_*.json)."
        ),
    )
    parser.add_argument(
        "--lofi-approve-gate",
        dest="lofi_approve_gate",
        type=int,
        choices=[1, 2],
        default=None,
        metavar="N",
        help="ECONOMIC_REEL_LOFI: approve Gate 1 (script) or Gate 2 (assembled prompts) and continue.",
    )
    parser.add_argument(
        "--test-preview",
        dest="test_preview",
        action="store_true",
        default=False,
        help=(
            "ECONOMIC_REEL_LOFI only: single-image aesthetic review (Flux Schnell + "
            "production grading + LOFI caption typography + watermark). "
            "Does NOT run script/validator/video/publish and does NOT write RAG history."
        ),
    )
    parser.add_argument(
        "--prompt",
        dest="prompt",
        default=None,
        metavar="TEXT",
        help=(
            "ECONOMIC_REEL_LOFI --test-preview: optional custom scene description. "
            "If omitted, a fixed seed-aligned baseline prompt is used."
        ),
    )
    parser.add_argument(
        "--economic",
        dest="economic",
        action="store_true",
        help="Force Gemini-only economic brain mode (research + captions).",
    )
    parser.add_argument(
        "--premium-relay",
        dest="premium",
        action="store_true",
        help="Force Gemini research + Claude 3.5 Sonnet captions (dual-LLM relay).",
    )
    parser.add_argument(
        "--publish-youtube",
        "--upload-youtube",
        dest="publish_youtube",
        action="store_true",
        default=False,
        help=(
            "Auto-upload compiled reels to YouTube AFTER generation as SCHEDULED "
            "(Programado): privacyStatus=private + publishAt staggered timestamps. "
            "Never uploads as unlisted/immediate. "
            "Uses isolated OAuth token per page at credentials/tokens/youtube_token_{page}.json. "
            "Alias: --upload-youtube. Overrides ENABLE_YOUTUBE_UPLOAD in .env."
        ),
    )
    parser.add_argument(
        "--yt-privacy",
        dest="yt_privacy",
        default=None,
        choices=["public", "unlisted", "private"],
        metavar="PRIVACY",
        help=(
            "IGNORED when --publish-youtube is set (forced to private + publishAt for Scheduled). "
            "Kept for legacy one-off scripts only."
        ),
    )
    parser.add_argument(
        "--schedule-uploads",
        dest="schedule_uploads",
        action="store_true",
        default=False,
        help=(
            "Legacy flag — scheduling is now ALWAYS ON with --publish-youtube "
            "(private + publishAt). Kept for backward compatibility."
        ),
    )
    parser.add_argument(
        "--interval-hours",
        dest="interval_hours",
        type=float,
        default=12.0,
        metavar="HOURS",
        help=(
            "Base interval in hours between scheduled posts when --publish-youtube "
            "is active. Video i publishes at now + interval_hours*(i+1) (+ random delay). "
            "Default: 12.0."
        ),
    )
    parser.add_argument(
        "--random-delay-max-minutes",
        dest="random_delay_max_minutes",
        type=int,
        default=60,
        metavar="MINUTES",
        help=(
            "Random additional delay in minutes (0 to X) added to each scheduled "
            "publishAt slot. Default: 60."
        ),
    )
    parser.add_argument(
        "--limit",
        dest="yt_upload_limit",
        type=int,
        default=None,
        metavar="N",
        help=(
            f"Global YouTube upload safety cap for this run "
            f"(default: {_YT_MAX_DAILY_UPLOADS}). Remaining videos are deferred "
            "to credentials/pending_youtube_uploads.json for the next run."
        ),
    )
    parser.add_argument(
        "--draw-style",
        dest="draw_style",
        default="SKETCH",
        choices=["NATURAL", "CARTOON", "SKETCH"],
        metavar="DRAW_STYLE",
        help=(
            "NATURAL (default): photorealistic cinematic image generation. "
            "CARTOON: Modern 2.5D flat illustration / vibrant stylized vector art. "
            "SKETCH: Forced graphite pencil illustration pipeline (auto-applied for "
            "SMART_BAIT / LONG_CAPTION_IMAGE / ECONOMIC_REEL on wonder_feed — "
            "this flag is ignored for those post types)."
        ),
    )
    parser.add_argument(
        "--clip-duration",
        dest="clip_duration",
        type=float,
        default=None,
        metavar="SECONDS",
        help=(
            "Target clip length in seconds for REFERENCE_BASED_REELS post type. "
            "Clamped to the page config CLIP_DURATION_MIN_S / CLIP_DURATION_MAX_S range. "
            "Default: midpoint of the configured range (e.g. 37 s for 15–60 s)."
        ),
    )
    parser.add_argument(
        "--render-approval-required",
        dest="render_approval_required",
        action="store_true",
        default=False,
        help=(
            "After scene images are saved to "
            "outputs/<page>/assets/<episode_id>/ as scene_01.png, … "
            "pause the pipeline, print that episode assets path, and wait "
            "for [ENTER] before audio/video compilation. Use Ctrl+C to abort. "
            "Default: off (continue automatically)."
        ),
    )
    parser.add_argument(
        "--test-images",
        dest="test_images",
        type=int,
        default=None,
        metavar="N",
        help=(
            "DEBUG MODE: generate the production script + the first N visual image "
            "prompts for --page, print every final prompt, generate N images in "
            "parallel (ThreadPoolExecutor max_workers=5) to "
            "outputs/<page_id>/test_previews/, then exit immediately. Bypasses "
            "ElevenLabs voiceover, MoviePy video compilation, and all "
            "YouTube/B2/ImgBB uploads."
        ),
    )
    parser.add_argument(
        "--resume-youtube-queue",
        dest="resume_youtube_queue",
        action="store_true",
        default=False,
        help=(
            "Read credentials/pending_youtube_uploads.json and attempt to publish "
            "any videos that were queued after a previous run hit YouTube's daily "
            "upload limit (~20 videos/channel/day). Scoped to --page when explicitly "
            "passed on the CLI, otherwise resumes queued uploads for ALL pages. "
            "Exits after resuming — does not generate new content."
        ),
    )
    from agents.mcp.model_api_flows import list_preset_names as _list_flow_presets

    parser.add_argument(
        "--model-api-flow",
        dest="model_api_flow",
        type=str,
        default=None,
        metavar="PRESET",
        help=(
            "Select a model_api_flows preset (provider/infra per media type). "
            "Always overrides .env ENABLE_REMOTE_GPU_WORKFLOWS in either direction. "
            f"Presets: {', '.join(_list_flow_presets())}."
        ),
    )
    parser.add_argument(
        "--img-production",
        dest="img_production",
        type=str,
        default=None,
        metavar="PROVIDER[/MODEL]",
        help=(
            "Per-media image override (wins over --model-api-flow for image only). "
            "Examples: gemini | gemini/models/gemini-2.5-flash-image | "
            "together/black-forest-labs/FLUX.1-schnell | remote_gpu | "
            "remote_gpu/flux_dev_lora_txt_to_img.json"
        ),
    )
    parser.add_argument(
        "--image-primary",
        dest="image_primary",
        type=str,
        default=None,
        metavar="MODEL",
        help=(
            "Image model SKU override (highest priority). "
            "Examples: models/gemini-3-pro-image-preview | "
            "models/gemini-2.5-flash-image | black-forest-labs/FLUX.1-schnell. "
            "Flux is the default for non-avatar runs. "
            "--avatar ON defaults to Flash Image (1K); pass this flag or "
            "--image-model gemini-pro to pin a SKU."
        ),
    )
    parser.add_argument(
        "--together_Juggernaut", "--together_juggernaut", "--juggernaut", "--Juggernaut",
        dest="together_juggernaut",
        action="store_true",
        default=False,
        help=(
            "Together AI image model override: "
            "Rundiffusion/Juggernaut-Lightning-Flux ($0.0017/img). "
            "When omitted, the engine keeps its current default image model. "
            "Aliases: --together_juggernaut, --juggernaut."
        ),
    )
    parser.add_argument(
        "--together_flux2dev",
        dest="together_flux2dev",
        action="store_true",
        default=False,
        help=(
            "Together AI image model override: "
            "black-forest-labs/FLUX.2-dev ($0.0154/img). "
            "Wins over --together_Juggernaut when both are set. "
            "ECONOMIC_REEL_LOFI ignores this — use --deepinfra_flux2dev."
        ),
    )
    parser.add_argument(
        "--deepinfra_flux2dev",
        dest="deepinfra_flux2dev",
        action="store_true",
        default=False,
        help=(
            "ECONOMIC_REEL_LOFI: generate stills on DeepInfra FLUX-2-dev "
            "($0.01 × w/1024 × h/1024 × steps/28; 720×1280 @ 28 ≈ $0.0088/img). "
            "Sets LOFI_FLUX_BACKEND=flux2-dev."
        ),
    )
    parser.add_argument(
        "--image-model",
        dest="image_model",
        type=str,
        default=None,
        choices=["flash", "gemini-pro"],
        metavar="TIER",
        help=(
            "Avatar / Gemini image tier. "
            "flash (default when --avatar ON): models/gemini-2.5-flash-image at 1K "
            "(~$0.039/img, ~$0.78 / 20). "
            "gemini-pro: models/gemini-3-pro-image-preview at 1K "
            "($0.03/img, ~$0.60 / 20). "
            "2K is never selected by this flag."
        ),
    )
    parser.add_argument(
        "--audio-production",
        dest="audio_production",
        type=str,
        default=None,
        metavar="PROVIDER",
        help=(
            "Per-media audio override (wins over --model-api-flow for audio only). "
            "Examples: elevenlabs | remote_gpu | remote_gpu/F5TTS_txt_to_audio.json"
        ),
    )
    parser.add_argument(
        "--video-production",
        dest="video_production",
        type=str,
        default=None,
        metavar="PROVIDER",
        help=(
            "Per-media video override (wins over --model-api-flow for video only). "
            "Examples: moviepy | remote_gpu | remote_gpu/wan2_2_img_to_video.json"
        ),
    )
    parser.add_argument(
        "--video-length",
        dest="video_length",
        type=float,
        default=None,
        metavar="SECONDS",
        help=(
            "Target reel duration in seconds for the shared scene pacing engine "
            "(plan_scenes). Wins over channels_config REEL_DURATION. "
            "Each channel/post_type supplies its own default when omitted "
            "(e.g. ancient_knowledge ~80)."
        ),
    )
    parser.add_argument(
        "--scene-duration",
        dest="scene_duration",
        type=str,
        default=None,
        metavar="SPEC",
        help=(
            "Scene pacing spec for plan_scenes (CLI > channel > factory equal). "
            "Forms: fixed:4 | progressive | "
            "progressive:start=4,step_every=3,step=1,cap=7.5 | equal"
        ),
    )
    parser.add_argument(
        "--scene-length",
        dest="scene_length",
        type=float,
        default=None,
        metavar="SECONDS",
        help=(
            "Force every B-roll still to this many seconds, overriding the "
            "3/3/4/4/5 hold pattern. Timeline = sum(holds); no last-still "
            "slack absorption."
        ),
    )
    args = parser.parse_args(argv)
    return args
