# -*- coding: utf-8 -*-
"""Imports that run after ACTIVE_PAGE and .env are bound.

Importing this module is the old middle of main.py: config, page
rebind, then the media and publisher stack. Function bodies live in
the sibling modules. Names imported here are re-exported to those
modules; they are not unused.
"""
# ruff: noqa: F401
from __future__ import annotations

import os
import re as _re
import shutil
import sys
from pathlib import Path

import argparse
import functools
import logging
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import config as app_config
from utils.pipeline_paths import outputs_root, page_outputs_dir, pipeline_logs_dir

from core.google_guardrail import bind_run_channel

_PRELOADED_PAGE = os.environ.get("ACTIVE_PAGE", "anna_protocol")
if (app_config.ACTIVE_PAGE or "").lower() != (_PRELOADED_PAGE or "").lower():
    app_config.bind_active_page(_PRELOADED_PAGE)
    print(
        f"[bootstrap] ACTIVE_PAGE rebound | cli={_PRELOADED_PAGE} "
        f"outputs={app_config.PAGE_OUTPUTS_DIR}",
        flush=True,
    )
else:
    bind_run_channel(_PRELOADED_PAGE)
from agents.writer.caption_engine import (
    CaptionEngine,
    build_gemini_researcher_instruction,
    economic_humanizer_instruction_preview,
    humanizer_preview_with_placeholder,
    long_caption_prompt_preview,
    build_batch_researcher_instruction,
    build_smart_bait_image_prompt,
    maybe_inject_horror_mutation,
)
from agents.media.b2_client import B2StorageCapError, B2VideoUploader
from agents.media.imgbb_client import upload_image_file_to_imgbb
from omni.adapters.youtube import publish_envelope as _yt_upload_short
from agents.posting.youtube_publisher import (
    build_credentials as _yt_build_credentials,
    build_youtube_client as _yt_build_client,
    verify_authorized_channel as _yt_verify_channel,
    resolve_youtube_token_path as _yt_token_path,
    get_or_create_playlist as _yt_get_or_create_playlist,  # noqa: F401 (available for future hooks)
    YouTubeQuotaExceededError,
    queue_pending_upload_from_envelope as _yt_queue_pending_upload,
    resume_pending_youtube_uploads as _yt_resume_pending_queue,
    DailyUploadSafetyGate as _YtDailyUploadSafetyGate,
    MAX_DAILY_UPLOADS as _YT_MAX_DAILY_UPLOADS,
)
from agents.media.audio_engine import (
    apply_voice_loudnorm,
    assert_voice_and_subtitles_ready,
    generate_voiceover,
    generate_voiceover_with_timestamps,
    generate_ambient_track,
    generate_master_mei_soundscape,
)
from agents.media.video_engine import compile_dynamic_reel
from core.cost_tracker import CostTracker
from core.interfaces.factory import ChannelFactory
from core.reel_sequence_engine import (
    compile_sequence_reel as _core_compile_sequence_reel,
    build_sequence_script_prompt as _build_sequence_script_prompt,
    build_hook_body_act_durations as _build_hook_body_act_durations,
    compute_audio_synced_act_durations as _compute_audio_synced_act_durations,
    compute_dense_act_count as _compute_dense_act_count,
    compute_hook_body_act_count as _compute_hook_body_act_count,
    segment_script_into_act_snippets as _segment_script_into_act_snippets,
    snippets_from_word_timings as _snippets_from_word_timings,
)
from core.scene_pacing import (
    describe_scene_plan as _describe_scene_plan,
    plan_scenes as _plan_scenes,
    scale_scene_durations as _scale_scene_durations,
)
from agents.media.brand_composer import (
    apply_text_overlay as _brand_apply_text,
    apply_logo_watermark as _brand_apply_logo,
    burn_text_on_video as _brand_burn_video,
    generate_text_quote_background as _brand_text_quote_bg,
)
from agents.media.content_library import (
    append_entry,
    build_library_metadata,
    dump_raw_research_to_log,
    recent_topics as _recent_library_topics,
)
from agents.media.durable_library import (
    PENDING_CAPTION,
    merge_update_json,
    path_under_engine,
    write_atomic_json,
)
from agents.rag.pdf_loader import list_pdf_relative_paths, load_digital_product_corpus
from agents.posting.post_planner import (
    append_planner_row,
    append_postplanner_xlsx_row,
    scheduled_bulk_post_display,
    update_planner_row,
)
from agents.media.providers.gemini_utils import build_model_chain, get_latest_model
from agents.media.persona_dna import contextual_cta_keyword
from agents.media.providers.image_provider import GeminiImageAdapter, get_image_adapter
from agents.media.subject_brain import imagine_subject, imagine_subject_instruction_preview, generate_bulk_topics
from agents.media.batch_planner import (
    BatchAngle,
    BatchUniquenessGuard,
    MAX_UNIQUENESS_RETRIES,
    plan_angles_matrix,
    plan_distinct_batch_topics,
    select_distinct_pool_topics,
    theme_key as _topic_theme_key,
)
from agents.media.text_utils import subject_slug
from agents.media.avatar_engine.visual_architect import (
    VisualArchitect,
    build_carousel_slide_prompt,
    ground_image_concept,
)
from channel_loader import (
    PageContext,
    load_page_context,
    resolve_default_avatar_mode,
    resolve_default_format,
    VALID_PAGES,
    VALID_AVATAR_MODES,
    VALID_FORMATS,
)
from utils.run_ledger import (
    PlannedModels,
    activate_run_ledger,
    configure_file_logging,
    ledger_file_path,
)
from agents.media.avatar_engine.mei_visual import (  # noqa: E402
    MEI_DNA_FALLBACK as _MEI_DNA_FALLBACK,
    _BAN_PLAIN as _MEI_BAN_PLAIN_MODERN,
    _PHOTOREAL as _MEI_PHOTOREAL,
    assert_avatar_exists as _assert_mei_avatar,
    build_master_mei_script_act_prompts as _mei_build_act_prompts,
    classify_mei_visual_theme as _classify_mei_visual_theme,
    resolve_master_mei_avatar_path as _resolve_mei_avatar_path,
)

_SEQUENCE_VIDEO_POST_TYPES = frozenset({"ECONOMIC_REEL", "WAN_REEL"})

_LOGO_ONLY_STILL_POST_TYPES = frozenset({"LONG_CAPTION_IMAGE", "CTA_CAPTION_IMAGE"})

_MIN_CACHED_STILL_BYTES = 2048

_VOICE_TOLERANCE_FRACTION: float = 0.15   # ±15 % of target video duration

_VOICE_MIN_OBSERVED_WPS: float = 0.8      # sanity floor for WPS math

_VOICE_MAX_OBSERVED_WPS: float = 4.0      # sanity ceiling for WPS math

_MAX_AUDIO_DURATION_RETRIES: int = 1
