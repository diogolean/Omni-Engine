# -*- coding: utf-8 -*-
"""cli steps moved out of main.py. Bodies are unchanged."""
from __future__ import annotations

from engine.runtime import (
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
from engine.log import _LOG
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
def cli() -> None:
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
            "core/economic_reel_lofi/store/aphorism_bank.json. "
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
    args = parser.parse_args()
    if getattr(args, "clear_reel_checkpoints", False):
        os.environ["REEL_CHECKPOINT"] = "clear"
    elif getattr(args, "ignore_reel_checkpoints", False):
        os.environ["REEL_CHECKPOINT"] = "ignore"
    if getattr(args, "hook_parallax", False):
        os.environ["REEL_HOOK_PARALLAX"] = "1"
    if getattr(args, "deepinfra_flux2dev", False):
        os.environ["LOFI_FLUX_BACKEND"] = "flux2-dev"
    # True CLI value before page locks (master_mei/anna) mutate args.avatar.
    _avatar_from_cli: str | None = args.avatar
    if getattr(args, "debug", False):
        os.environ["ENGINE_DEBUG"] = "true"
        app_config.ENGINE_DEBUG = True

    # Together CLI flags pin the image SKU before flow resolution so
    # remote_gpu env defaults cannot steal the image provider.
    _together_cli = _together_cli_image_model(args)
    if _together_cli and not getattr(args, "img_production", None):
        args.img_production = f"together/{_together_cli[1]}"

    # ── model_api_flows: resolve + apply before any generation ──────────────
    # Precedence: per-media flags > --model-api-flow > .env default.
    from agents.mcp.model_api_flows import (
        apply_production_flow as _apply_flow,
        bootstrap_validate_all_presets as _validate_flow_presets,
        resolve_production_flow as _resolve_flow,
    )

    try:
        _validate_flow_presets()
        _flow_explicit = any(
            [
                getattr(args, "model_api_flow", None),
                getattr(args, "img_production", None),
                getattr(args, "audio_production", None),
                getattr(args, "video_production", None),
            ]
        )
        _resolved_flow = _resolve_flow(
            preset_name=getattr(args, "model_api_flow", None),
            img_production=getattr(args, "img_production", None),
            audio_production=getattr(args, "audio_production", None),
            video_production=getattr(args, "video_production", None),
        )
        _apply_flow(_resolved_flow, explicit=_flow_explicit)
        args.resolved_model_api_flow = _resolved_flow
        # Fail fast: ElevenLabs Key ID masquerading as secret (api_key_id_used_as_api_key)
        if getattr(_resolved_flow.audio, "provider", "") == "elevenlabs":
            try:
                app_config.assert_elevenlabs_api_key_usable()
            except RuntimeError as _el_exc:
                raise SystemExit(f"[elevenlabs] {_el_exc}") from _el_exc
    except (ValueError, FileNotFoundError) as _flow_exc:
        raise SystemExit(f"[model_api_flows] {_flow_exc}") from _flow_exc

    if args.resume_youtube_queue:
        _explicit_page = any(a in ("--channel", "--page") or a.startswith(("--channel=", "--page=")) for a in sys.argv[1:])
        _resume_page = args.page if _explicit_page else None
        print(
            "[YouTube] --resume-youtube-queue: resuming pending upload queue"
            + (f" for page='{_resume_page}' …" if _resume_page else " for ALL pages …")
        )
        _yt_resume_pending_queue(
            page_name=_resume_page,
            limit=getattr(args, "yt_upload_limit", None),
        )
        return

    # ── PERMANENT WONDER_FEED STYLE LOCK ───────────────────────────────────
    # Fires BEFORE any config lookups so no legacy format bucket can override.
    # Idempotent — the later guard at runtime still runs for belt-and-suspenders.
    if getattr(args, "page", "").lower() == "wonder_feed":
        _wf_pt = getattr(args, "post_type", "").upper()
        if _wf_pt in ("SMART_BAIT", "LONG_CAPTION_IMAGE", "ECONOMIC_REEL"):
            args.draw_style = "SKETCH"
        if _wf_pt == "ECONOMIC_REEL":
            # Force post_format so resolve_default_format() cannot assign IMAGE_AVATAR
            args.post_format = "DYNAMIC_REEL"
        if _wf_pt == "ECONOMIC_REEL_LOFI":
            args.post_format = "DYNAMIC_REEL"
            args.draw_style = "SKETCH"

    # Isolated channel (ancient_knowledge): visual rules + format come from
    # the adapter — never the hardcoded page_id sketch/photoreal branch.
    _boot_channel = ChannelFactory.from_env()
    if ChannelFactory.is_isolated(_boot_channel.channel_id):
        _ak_pt = getattr(args, "post_type", "").upper()
        if _ak_pt == "ECONOMIC_REEL":
            args.post_format = "DYNAMIC_REEL"
        args.draw_style = str(
            (_boot_channel.get_visual_rules() or {}).get("draw_style") or "NATURAL"
        )

    # ── MASTER_MEI (SUPER) STYLE & FORMAT LOCK ─────────────────────────────
    # Cinematic martial photorealism + avatar ON; sequence reel for ECONOMIC_REEL.
    if getattr(args, "page", "").lower() == "master_mei":
        _mm_pt = getattr(args, "post_type", "").upper()
        if _mm_pt == "ECONOMIC_REEL":
            args.post_format = "DYNAMIC_REEL"
        args.draw_style = "NATURAL"
        # Default avatar ON unless CLI explicitly set --avatar
        if getattr(args, "avatar", None) is None:
            args.avatar = "ON"

    # ── MOMMA_CIRCLE FORMAT LOCK ────────────────────────────────────────────
    # Reference-based pipeline by default; ECONOMIC_REEL_LOFI is an explicit escape hatch.
    if getattr(args, "page", "").lower() == "momma_circle":
        _mc_pt = getattr(args, "post_type", "").upper()
        if _mc_pt == "ECONOMIC_REEL_LOFI":
            args.post_format = "DYNAMIC_REEL"
            args.draw_style = "SKETCH"
        else:
            args.post_type = "REFERENCE_BASED_REELS"
            args.post_format = "REFERENCE_BASED_REELS"
            args.draw_style = "NATURAL"

    # ── ANNA_PROTOCOL STYLE LOCK ───────────────────────────────────────────
    # Photoreal stills (factory --draw-style default is SKETCH).
    # --avatar ON defaults to Flash Image 1K, not Gemini Pro.
    if getattr(args, "page", "").lower() == "anna_protocol":
        args.draw_style = "NATURAL"

    if getattr(args, "post_type", "").upper() == "CTA_CAPTION_IMAGE":
        if getattr(args, "page", "").lower() != "anna_protocol":
            raise SystemExit(
                "CTA_CAPTION_IMAGE is only available for --page anna_protocol. "
                "Example: python main.py --page anna_protocol "
                "--post-type CTA_CAPTION_IMAGE --quantity 1"
            )

    if args.economic and args.premium:
        raise SystemExit("Choose either --economic or --premium-relay, not both.")

    # ------------------------------------------------------------------
    # Build PageContext from parsed flags.
    # avatar and format fall back to page-level defaults if not specified.
    # ------------------------------------------------------------------
    page_id: str = args.page
    if (page_id or "").strip() and (app_config.ACTIVE_PAGE or "").lower() != page_id.lower():
        app_config.bind_active_page(page_id)
    elif (page_id or "").strip():
        bind_run_channel(page_id)

    # Load page config to read per-page defaults.
    _tmp_page_cfg: dict = {}
    try:
        from channel_loader import (  # noqa: PLC0415
            _CHANNELS_CONFIG_ROOT,
            _LEGACY_PAGES_CONFIG_ROOT,
            _load_page_config,
        )
        _cfg_dir = _CHANNELS_CONFIG_ROOT / page_id
        if not _cfg_dir.is_dir():
            _legacy = _LEGACY_PAGES_CONFIG_ROOT / page_id
            if _legacy.is_dir():
                _cfg_dir = _legacy
        _tmp_page_cfg = _load_page_config(_cfg_dir, page_id)
    except Exception:  # noqa: BLE001
        pass

    avatar_mode: str = (
        args.avatar
        if args.avatar is not None
        else resolve_default_avatar_mode(_tmp_page_cfg)
    )
    post_format: str = (
        args.post_format
        if args.post_format is not None
        else resolve_default_format(_tmp_page_cfg)
    )
    # ECONOMIC_REEL has its own video-compilation pipeline — stop it routing into
    # the static image post_format buckets (IMAGE_AVATAR etc.) so the bootstrap
    # display and any format-sensitive guards all see the correct type.
    if getattr(args, "post_type", "").upper() == "ECONOMIC_REEL":
        post_format = "DYNAMIC_REEL"
    if getattr(args, "post_type", "").upper() == "ECONOMIC_REEL_LOFI":
        post_format = "DYNAMIC_REEL"
    if getattr(args, "post_type", "").upper() == "WAN_REEL":
        post_format = "DYNAMIC_REEL"
    # REFERENCE_BASED_REELS uses its own engine path — keep the format as-is.
    if getattr(args, "post_type", "").upper() == "REFERENCE_BASED_REELS":
        post_format = "REFERENCE_BASED_REELS"

    try:
        page_ctx = load_page_context(page_id, avatar_mode=avatar_mode, post_format=post_format)
    except ValueError as ve:
        raise SystemExit(str(ve)) from ve

    if getattr(args, "image_primary", None):
        _img_primary = str(args.image_primary).strip()
        if _img_primary:
            page_ctx.page_cfg["IMAGE_PRIMARY_CLI"] = _img_primary
            page_ctx.page_cfg["IMAGE_MODEL_OVERRIDE"] = _img_primary
            page_ctx.page_cfg["IMAGE_PRIMARY"] = _img_primary
            print(f"[bootstrap] image_primary={_img_primary} (CLI override)")
    if _together_cli:
        _together_label, _together_model = _together_cli
        page_ctx.page_cfg["IMAGE_PRIMARY_CLI"] = _together_model
        page_ctx.page_cfg["IMAGE_MODEL_OVERRIDE"] = _together_model
        page_ctx.page_cfg["IMAGE_PRIMARY"] = _together_model
        app_config.TOGETHER_IMAGE_MODEL = _together_model
        os.environ["TOGETHER_IMAGE_MODEL"] = _together_model
        print(
            f"[bootstrap] {_together_label}={_together_model} "
            "(CLI override → Together AI)"
        )
    if getattr(args, "image_model", None) and not page_ctx.page_cfg.get("IMAGE_PRIMARY_CLI"):
        _alias = app_config.resolve_image_model_alias(args.image_model)
        if _alias:
            page_ctx.page_cfg["IMAGE_PRIMARY_CLI"] = _alias
            page_ctx.page_cfg["IMAGE_MODEL_OVERRIDE"] = _alias
            page_ctx.page_cfg["IMAGE_PRIMARY"] = _alias
            page_ctx.page_cfg["IMAGE_MODEL_TIER"] = str(args.image_model)
            print(
                f"[bootstrap] image_model={args.image_model} → {_alias} "
                f"(size={getattr(app_config, 'GEMINI_IMAGE_SIZE', '1K')})"
            )
    if getattr(args, "img_production", None):
        page_ctx.page_cfg["IMG_PRODUCTION_CLI"] = str(args.img_production).strip()
    if _avatar_from_cli is not None:
        page_ctx.page_cfg["AVATAR_CLI"] = str(_avatar_from_cli).upper()
        print(f"[bootstrap] avatar={page_ctx.page_cfg['AVATAR_CLI']} (CLI)")
        _img_prod_raw = str(page_ctx.page_cfg.get("IMG_PRODUCTION_CLI") or "").strip().lower()
        _provider_forced = _img_prod_raw.startswith(
            ("together", "remote_gpu", "flux")
        )
        if (
            page_ctx.page_cfg["AVATAR_CLI"] == "ON"
            and not page_ctx.page_cfg.get("IMAGE_PRIMARY_CLI")
            and not _provider_forced
        ):
            _flash = getattr(
                app_config, "GEMINI_FLASH_IMAGE_MODEL", "models/gemini-2.5-flash-image"
            )
            page_ctx.page_cfg["IMAGE_MODEL_OVERRIDE"] = _flash
            page_ctx.page_cfg["IMAGE_MODEL_TIER"] = "flash"
            print(
                f"[bootstrap] --avatar ON → image_primary={_flash} "
                f"(1K Flash Image; pass --image-model gemini-pro to opt into Pro)"
            )

    _preflight_gemini_image_batch_cost(
        page_ctx=page_ctx,
        quantity=int(getattr(args, "quantity", 1) or 1),
    )

    # Scene pacing overrides — CLI > channels_config > factory default.
    if getattr(args, "video_length", None) is not None:
        page_ctx.page_cfg["VIDEO_LENGTH_OVERRIDE"] = float(args.video_length)
        page_ctx.page_cfg["REEL_DURATION"] = float(args.video_length)
        print(
            f"[bootstrap] video_length={float(args.video_length):.0f}s "
            "(CLI override → plan_scenes)"
        )
    if getattr(args, "scene_duration", None):
        page_ctx.page_cfg["SCENE_DURATION"] = str(args.scene_duration).strip()
        print(
            f"[bootstrap] scene_duration={page_ctx.page_cfg['SCENE_DURATION']!r} "
            "(CLI override → plan_scenes)"
        )
    if getattr(args, "scene_length", None):
        page_ctx.page_cfg["SCENE_LENGTH"] = float(args.scene_length)
        print(
            f"[bootstrap] scene_length={float(args.scene_length):.1f}s "
            "(CLI override → uniform still holds)"
        )

    economic_choice: bool | None
    if args.premium:
        economic_choice = False
    elif args.economic:
        economic_choice = True
    else:
        economic_choice = None

    econ_resolved = economic_choice if economic_choice is not None else app_config.ECONOMIC_BRAIN_MODE
    # Page-level override: respect ECONOMIC_BRAIN_MODE from page_config.py when
    # the CLI did not explicitly pass --economic or --premium.
    # This ensures that ancient_knowledge (ECONOMIC_BRAIN_MODE=True) always gets
    # the nano/flash image model, regardless of the global app_config default.
    if economic_choice is None and _tmp_page_cfg.get("ECONOMIC_BRAIN_MODE") is not None:
        econ_resolved = bool(_tmp_page_cfg["ECONOMIC_BRAIN_MODE"])
        _LOG.info(
            "Page-level economic override | page=%s ECONOMIC_BRAIN_MODE=%s (applied before model verification)",
            page_id, econ_resolved,
        )

    if args.test_images is not None:
        run_test_images_debug_mode(
            topic=(args.topic or None),
            page_id=page_id,
            avatar_mode=avatar_mode,
            post_format=post_format,
            n=args.test_images,
            economic=bool(econ_resolved),
        )
        return

    if str(getattr(args, "post_type", "")).upper() == "ECONOMIC_REEL_LOFI":
        # LOFI owns its writer/image routing. Do not probe Gemini's generic
        # caption/image chains, especially on --script-only where no image
        # provider is used at all.
        planned_models = PlannedModels(
            image_primary_id=(
                "models/gemini-3.1-flash-image"
                if getattr(args, "lofi_image_provider", "together") == "gemini"
                else "black-forest-labs/FLUX.1-schnell"
            ),
            research_primary_id=(
                "lofi-writer-runtime"
                if not getattr(args, "script_only", False)
                else "lofi-script-only"
            ),
            humanizer_summary=(
                f"LOFI writer mode `{getattr(args, 'lofi_mode', 'emotional')}`"
            ),
        )
    else:
        planned_models = _snapshot_verified_models(
            economic_brain_mode=econ_resolved,
            page_ctx=page_ctx,
        )

    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s | %(message)s",
        force=True,
    )
    _silence_noisy_http_loggers()

    log_path, ts_token = ledger_file_path(app_config.ENGINE_ROOT)
    configure_file_logging(log_path)

    for h in logging.root.handlers:
        if getattr(h, "_engine_run_journal", False):
            continue
        h.setLevel(logging.WARNING)

    activate_run_ledger(log_path, planned=planned_models)
    logging.getLogger(__name__).info(
        "=== ENGINE RUN BEGIN | page=%s | avatar=%s | format=%s | log=%s | ts=%s ===",
        page_id, avatar_mode, post_format, log_path, ts_token,
    )
    _flow_for_log = getattr(args, "resolved_model_api_flow", None)
    _active_post_type_early = args.post_type.upper() if hasattr(args, "post_type") else ""
    if _flow_for_log is not None:
        logging.getLogger(__name__).info(
            "MODEL_API_FLOW | %s", _flow_for_log.summary_line(),
        )
        if _active_post_type_early == "ECONOMIC_REEL_LOFI":
            # Clarify: LOFI image gen hard-bypasses this preset (Together Schnell, no LoRA).
            print(
                f"[bootstrap] MODEL_API_FLOW (global env preset, NOT used for "
                f"ECONOMIC_REEL_LOFI images) | {_flow_for_log.summary_line()}"
            )
            _lofi_provider = getattr(args, "lofi_image_provider", "together")
            _lofi_flux = str(os.environ.get("LOFI_FLUX_BACKEND") or "schnell").strip()
            if _lofi_provider == "gemini":
                print(
                    "[bootstrap] ECONOMIC_REEL_LOFI image provider | "
                    "google / Gemini Flash Image chain / no reference image"
                )
            elif _lofi_flux.lower() in {
                "flux2",
                "flux2-dev",
                "flux.2-dev",
                "flux2dev",
                "deepinfra_flux2dev",
            }:
                print(
                    "[bootstrap] ECONOMIC_REEL_LOFI image provider FORCED | "
                    "deepinfra / black-forest-labs/FLUX-2-dev / lora=OFF "
                    "(--deepinfra_flux2dev; ignores Together / MODEL_API_FLOW)"
                )
            else:
                print(
                    "[bootstrap] ECONOMIC_REEL_LOFI image provider FORCED | "
                    "together.ai / black-forest-labs/FLUX.1-schnell / lora=OFF "
                    "(ignores ENABLE_REMOTE_GPU_WORKFLOWS / MODEL_API_FLOW)"
                )
        else:
            print(f"[bootstrap] MODEL_API_FLOW | {_flow_for_log.summary_line()}")

    print(f"[bootstrap] Detailed run log: {log_path}")

    topic_raw = args.topic or None
    if topic_raw is None and args.test:
        topic_raw = input("Topic (optional, press Enter to rely on scaffold placeholder): ").strip() or ""

    # For wonder_feed graphite post types, force the draw_style to SKETCH regardless
    # of what the user passed on the CLI — the page's BASE_GRAPHITE_PROMPT is the
    # single source of truth, and SKETCH is the only correct flag for that pipeline.
    _active_post_type = args.post_type.upper() if hasattr(args, "post_type") else ""
    if (
        args.page.lower() == "wonder_feed"
        and _active_post_type in ("SMART_BAIT", "LONG_CAPTION_IMAGE", "ECONOMIC_REEL", "WAN_REEL", "CAROUSEL")
    ):
        args.draw_style = "SKETCH"

    envelope: dict[str, Any] | None = None

    # ── ECONOMIC_REEL_LOFI early dispatch (isolated from ECONOMIC_REEL) ───
    if _active_post_type == "ECONOMIC_REEL_LOFI":
        # Aesthetic still review — never enters production / publish path.
        if getattr(args, "test_preview", False):
            try:
                from core.economic_reel_lofi.test_preview import (
                    run_lofi_test_preview,
                )

                _preview = run_lofi_test_preview(
                    page_id=page_id,
                    prompt=getattr(args, "prompt", None),
                    image_provider=getattr(args, "lofi_image_provider", "together"),
                )
                print(f"\n[ECONOMIC_REEL_LOFI test-preview] {_preview.get('output_png')}")
            except Exception as prev_exc:  # noqa: BLE001
                if isinstance(prev_exc, KeyboardInterrupt):
                    raise
                _LOG.error("LOFI test-preview failed: %s", prev_exc, exc_info=True)
                print(
                    f"[ECONOMIC_REEL_LOFI test-preview] ERROR: "
                    f"{type(prev_exc).__name__}: {prev_exc}"
                )
                sys.exit(1)
            return

        try:
            import core.economic_reel_lofi  # noqa: F401 — registers runner
            from core.post_type_registry import get_post_type_runner

            _lofi_runner = get_post_type_runner("ECONOMIC_REEL_LOFI")
            if _lofi_runner is None:
                raise SystemExit(
                    "ECONOMIC_REEL_LOFI runner missing from POST_TYPE_REGISTRY"
                )
            _lofi_module = getattr(args, "module", None) or "relationship"
            _lofi_duration = getattr(args, "duration", None)
            if bool(getattr(args, "lofi_allow_mixed_era", False)):
                os.environ["LOFI_ALLOW_MIXED_ERA"] = "1"
            envelope = _lofi_runner(
                page_id=page_id,
                quantity=args.quantity,
                duration=_lofi_duration,
                module=_lofi_module,
                theme=getattr(args, "lofi_theme", None),
                subtheme=getattr(args, "lofi_subtheme", None),
                writer_mode=getattr(args, "lofi_mode", "emotional"),
                seed_quote=getattr(args, "lofi_seed_quote", None),
                aphorism_id=getattr(args, "lofi_aphorism_id", None),
                image_provider=getattr(args, "lofi_image_provider", "together"),
                script_only=bool(getattr(args, "script_only", False)),
                stills_only=bool(getattr(args, "stills_only", False)),
                dry_run=bool(getattr(args, "dry_run", False)),
                locked_scripts=getattr(args, "lofi_scripts", None),
                review_required=(
                    False if bool(getattr(args, "lofi_no_review", False)) else None
                ),
                resume_from=getattr(args, "lofi_resume_from", None),
                approve_gate=getattr(args, "lofi_approve_gate", None),
                # Reuse existing per-page tree: outputs/<page>/{clips,assets}/
                outputs_dir=page_ctx.outputs_dir,
            )
        except Exception as lofi_exc:  # noqa: BLE001
            if isinstance(lofi_exc, KeyboardInterrupt):
                raise
            _LOG.error("ECONOMIC_REEL_LOFI failed: %s", lofi_exc, exc_info=True)
            print(f"[ECONOMIC_REEL_LOFI] ERROR: {type(lofi_exc).__name__}: {lofi_exc}")
            sys.exit(1)

        if envelope:
            print(
                f"\n[ECONOMIC_REEL_LOFI] Completed "
                f"{envelope.get('successful', 0)}/{args.quantity} reel(s)."
            )
            for _it in envelope.get("items") or []:
                if _it.get("video_path"):
                    print(f"  Video : {_it['video_path']}")
                if _it.get("work_dir"):
                    print(f"  Stills: {_it['work_dir']}")
                if _it.get("meta_path"):
                    print(f"  Meta  : {_it['meta_path']}")
        return

    # ── WAN_REEL production (registry) — reuses produce() / variant worker ──
    # Smoke test remains core.wan_reel_engine.run_wan_reel_test (not CLI).
    if _active_post_type == "WAN_REEL":
        import core.wan_reel_production  # noqa: F401 — registers runner
        from core.post_type_registry import get_post_type_runner
        from core.wan_reel_production import assert_wan_reel_page

        assert_wan_reel_page(page_id)
        if get_post_type_runner("WAN_REEL") is None:
            raise SystemExit("WAN_REEL runner missing from POST_TYPE_REGISTRY")
        print(
            f"[WAN_REEL] Production path | page={page_id} | "
            "script/TTS/stills via ECONOMIC_REEL worker, Wan img2vid at compile."
        )

    # ── REFERENCE_BASED_REELS early dispatch ──────────────────────────────
    # Bypasses the full Gemini-image produce() pipeline — uses raw footage clips.
    if _active_post_type == "REFERENCE_BASED_REELS":
        try:
            from core.reference_reel_engine import ReferenceReelEngine
            _ref_engine = ReferenceReelEngine(
                page_ctx,
                outputs_dir=page_ctx.outputs_dir,
                api_key_gemini=app_config.GEMINI_API_KEY,
                api_key_elevenlabs=app_config.ELEVENLABS_API_KEY,
            )
            postplanner_dir = page_ctx.outputs_dir / "postplanner"
            envelope = _ref_engine.run(
                quantity=args.quantity,
                topic=topic_raw or None,
                clip_duration=getattr(args, "clip_duration", None),
                postplanner_dir=postplanner_dir,
                run_stamp=ts_token,
                posting_slot_display="",
                publish_to_b2=True,
            )
        except Exception as ref_exc:  # noqa: BLE001
            if isinstance(ref_exc, KeyboardInterrupt):
                raise
            _LOG.error("REFERENCE_BASED_REELS failed: %s", ref_exc, exc_info=True)
            print(f"[refReel] ERROR: {type(ref_exc).__name__}: {ref_exc}")
            sys.exit(1)

        # Print lightweight summary and skip the YT block if no videos
        if envelope:
            _items = envelope.get("items") or []
            print(f"\n[refReel] Completed {envelope.get('successful', 0)}/{args.quantity} reel(s).")
            for _it in _items:
                if _it.get("video_path"):
                    print(f"  Video : {_it['video_path']}")
                if _it.get("b2_url"):
                    print(f"  B2 URL: {_it['b2_url']}")
        return  # skip produce() + YT block for this post type

    try:
        envelope = produce(
            topic_raw,
            quantity=args.quantity,
            skip_image=args.skip_image,
            skip_caption=args.skip_caption,
            test_mode=args.test,
            economic_brain_mode=economic_choice,
            bootstrap_models=planned_models,
            page_ctx=page_ctx,
            cta_enabled=(args.cta.upper() != "OFF"),
            post_type=args.post_type.upper(),
            carousel_quantity=getattr(args, "carousel_quantity", 3) or 3,
            image_style=args.draw_style.upper(),
            multi_variant=bool(getattr(args, "multi_variant", False)),
            text_overlay=(
                True if getattr(args, "text_overlay", None) == "ON"
                else False if getattr(args, "text_overlay", None) == "OFF"
                else None
            ),
            render_approval_required=bool(
                getattr(args, "render_approval_required", False)
            ),
            agentic_pipeline=getattr(args, "agentic_pipeline", None),
        )
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, KeyboardInterrupt):
            raise
        if _looks_like_upstream_api_failure(exc):
            _emit_clean_api_error(exc)
            _LOG.error("Run aborted due to upstream API failure.", exc_info=True)
            sys.exit(1)
        raise

    logging.getLogger(__name__).info(
        "=== ENGINE RUN COMPLETE | page=%s | log=%s subject=%s ===",
        page_id,
        log_path,
        envelope.get("resolved_subject") if envelope else None,
    )

    if isinstance(envelope.get("mode"), str) and envelope["mode"] == "test":
        _print_test_footer()
    else:
        _print_production_summary(envelope, page_ctx=page_ctx)

    # ── PHASE YT: YouTube Shorts auto-publish with per-page token isolation ──
    # Triggered by --publish-youtube / --upload-youtube OR ENABLE_YOUTUBE_UPLOAD.
    # Each --page uses credentials/tokens/youtube_token_{page}.json exclusively.
    #
    # STRICT: When publishing is active, uploads are ALWAYS Scheduled (Programado):
    #   privacyStatus = "private" + status.publishAt = staggered ISO timestamp.
    # Never upload as unlisted/public immediate when --publish-youtube is set.
    _should_publish_yt = getattr(args, "publish_youtube", False) or app_config.ENABLE_YOUTUBE_UPLOAD
    # Force continuous schedule queue whenever publishing (CLI flag becomes redundant but honored)
    _schedule_uploads = bool(_should_publish_yt) or bool(getattr(args, "schedule_uploads", False))
    if getattr(args, "schedule_uploads", False) and not _should_publish_yt:
        print(
            "[YouTube] --schedule-uploads requires --publish-youtube "
            "(or ENABLE_YOUTUBE_UPLOAD=true). Skipping schedule."
        )
        _schedule_uploads = False
    if _should_publish_yt and envelope and envelope.get("mode") != "test":
        _yt_privacy = "private"  # Scheduled / Programado requires private + publishAt
        _interval_h = float(getattr(args, "interval_hours", 12.0) or 12.0)
        if _interval_h <= 0:
            _interval_h = 12.0
        _rand_max_m = int(getattr(args, "random_delay_max_minutes", 60) or 0)
        if _rand_max_m < 0:
            _rand_max_m = 0

        from modules.reel_qa.publish import row_publishable

        _yt_candidates = [
            row for row in (envelope.get("items") or [])
            if row.get("video_path")
        ]
        _yt_rows = [row for row in _yt_candidates if row_publishable(row)]
        _yt_blocked = [row for row in _yt_candidates if not row_publishable(row)]
        if _yt_blocked:
            print(
                f"[VisualQA] YouTube skipped {len(_yt_blocked)} reel(s) "
                "without verdict=pass"
            )
            _LOG.error(
                "REEL QA blocked YouTube | n=%d",
                len(_yt_blocked),
            )
        if not _yt_rows:
            print("[YouTube] No compiled video found in this run — skipping upload.")
        else:
            _yt_secrets = app_config.YOUTUBE_CLIENT_SECRETS
            _yt_token_dir = app_config.YOUTUBE_TOKEN_DIR
            _yt_token_file = _yt_token_path(page_id, _yt_token_dir)
            print(
                f"[YouTube] Page '{page_id}' → token file: {_yt_token_file}"
            )

            _yt_client = None
            try:
                _yt_creds = _yt_build_credentials(
                    page_id,
                    _yt_secrets,
                    _yt_token_dir,
                )
                _yt_client = _yt_build_client(_yt_creds)
                _yt_verify_channel(_yt_client, page_name=page_id)
            except Exception as _auth_exc:
                _LOG.warning(
                    "YT auth/init failed (%s) — uploads will retry per-row auth.",
                    _auth_exc,
                )

            _sched_anchor = datetime.now(timezone.utc)
            _yt_gate = _YtDailyUploadSafetyGate(
                limit=getattr(args, "yt_upload_limit", None)
            )
            print(
                f"[YouTube Scheduler] Scheduling {len(_yt_rows)} video(s) as Programado | "
                f"privacy=private + publishAt | interval={_interval_h:g} h | "
                f"random delay=0–{_rand_max_m} min | "
                f"safety cap={_yt_gate.limit} | "
                f"anchor={_sched_anchor.strftime('%Y-%m-%d %H:%M:%S')} UTC"
            )
            _LOG.info(
                "YT schedule-uploads FORCED | page=%s videos=%d interval_h=%s "
                "rand_max_m=%d safety_cap=%d",
                page_id,
                len(_yt_rows),
                _interval_h,
                _rand_max_m,
                _yt_gate.limit,
            )

            for _i, _yt_row in enumerate(_yt_rows):
                if not _yt_gate.can_upload():
                    _yt_gate.notify_halt()
                    _remaining_rows = _yt_rows[_i:]
                    print(
                        f"[YouTube] Queuing remaining {len(_remaining_rows)} video(s) → "
                        "credentials/pending_youtube_uploads.json"
                    )
                    for _r_offset, _pending_row in enumerate(_remaining_rows):
                        _pending_i = _i + _r_offset
                        _pending_extra_min = (
                            random.randint(0, _rand_max_m) if _rand_max_m > 0 else 0
                        )
                        _pending_publish_at = _sched_anchor + timedelta(
                            hours=_interval_h * (_pending_i + 1),
                            minutes=_pending_extra_min,
                        )
                        _yt_queue_pending_upload(
                            row=_pending_row,
                            page_name=page_id,
                            privacy_status="private",
                            publish_at=_pending_publish_at,
                            reason="global_safety_cap",
                        )
                    break

                _extra_min = random.randint(0, _rand_max_m) if _rand_max_m > 0 else 0
                _publish_at = _sched_anchor + timedelta(
                    hours=_interval_h * (_i + 1),
                    minutes=_extra_min,
                )
                # YouTube API: Scheduled = private + publishAt (never unlisted)
                _privacy_for_row = "private"
                _iso = _publish_at.strftime("%Y-%m-%dT%H:%M:%SZ")
                print(
                    f"[YouTube Scheduler] Video {_i + 1}/{len(_yt_rows)} "
                    f"'{Path(_yt_row.get('video_path', '?')).name}' → "
                    f"SCHEDULED {_publish_at.strftime('%Y-%m-%d %H:%M:%S')} UTC "
                    f"(privacyStatus=private, publishAt={_iso}, +{_extra_min} min random)"
                )

                try:
                    _yt_vid_id, _yt_url, _yt_pa = _yt_upload_short(
                        row=_yt_row,
                        page_name=page_id,
                        privacy_status=_privacy_for_row,
                        publish_at=_publish_at,
                        client_secrets_path=_yt_secrets,
                        token_dir=_yt_token_dir,
                        youtube=_yt_client,
                    )
                    if _yt_vid_id:
                        _yt_row["youtube_video_id"] = _yt_vid_id
                        _yt_row["youtube_url"] = _yt_url
                        _yt_gate.record_success()
                        if _yt_pa:
                            _yt_row["youtube_scheduled_at"] = _yt_pa.strftime(
                                "%Y-%m-%d %H:%M UTC"
                            )
                            print(
                                f"[YouTube] ✓ Scheduled release confirmed → "
                                f"{_yt_pa.strftime('%Y-%m-%d %H:%M:%S')} UTC | {_yt_url}"
                            )
                        _LOG.info(
                            "YouTube upload OK | page=%s  id=%s  url=%s  publish_at=%s",
                            page_id,
                            _yt_vid_id,
                            _yt_url,
                            _yt_pa.strftime("%Y-%m-%d %H:%M UTC") if _yt_pa else "immediate",
                        )
                except YouTubeQuotaExceededError as _yt_quota_exc:
                    _LOG.warning(
                        "[YouTube] Daily upload limit (20 videos) reached for this channel."
                    )
                    _remaining_rows = _yt_rows[_i:]
                    print(
                        "[YouTube] Daily upload limit (20 videos) reached for this channel. "
                        f"Queuing remaining {len(_remaining_rows)} video(s) → "
                        "credentials/pending_youtube_uploads.json"
                    )
                    for _r_offset, _pending_row in enumerate(_remaining_rows):
                        _pending_i = _i + _r_offset
                        _pending_extra_min = (
                            random.randint(0, _rand_max_m) if _rand_max_m > 0 else 0
                        )
                        _pending_publish_at = _sched_anchor + timedelta(
                            hours=_interval_h * (_pending_i + 1),
                            minutes=_pending_extra_min,
                        )
                        _yt_queue_pending_upload(
                            row=_pending_row,
                            page_name=page_id,
                            privacy_status="private",
                            publish_at=_pending_publish_at,
                            reason="daily_upload_limit_exceeded",
                        )
                    _LOG.info(
                        "YT quota reached | page=%s queued=%d | %s",
                        page_id, len(_remaining_rows), _yt_quota_exc,
                    )
                    # Stop attempting further uploads this run — the pipeline
                    # completes normally; queued videos resume via
                    # --resume-youtube-queue once the daily cap rolls over.
                    break
                except Exception as _yt_exc:  # noqa: BLE001
                    _LOG.error(
                        "YouTube upload failed for '%s': %s",
                        _yt_row.get("video_path", "?"),
                        _yt_exc,
                        exc_info=True,
                    )
                    print(
                        f"[YouTube] Upload FAILED for "
                        f"{Path(_yt_row.get('video_path', '?')).name}: "
                        f"{type(_yt_exc).__name__}: {_yt_exc}"
                    )


from engine.acts import _looks_like_upstream_api_failure, _silence_noisy_http_loggers  # noqa: E402
from engine.orchestrator import produce, run_test_images_debug_mode  # noqa: E402
from engine.reporting import _emit_clean_api_error, _preflight_gemini_image_batch_cost, _print_production_summary, _print_test_footer, _snapshot_verified_models  # noqa: E402
