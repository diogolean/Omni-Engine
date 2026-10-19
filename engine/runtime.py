# -*- coding: utf-8 -*-
"""Imports that run after ACTIVE_PAGE and .env are bound.

Importing this module is the old middle of main.py: config, page
rebind, then the media and publisher stack. Function bodies live in
the sibling modules.
"""
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

_GEO_ANCHORS: list[tuple[tuple[str, ...], str]] = [
    (("nazca", "geoglyph", "nazca lines"),
     "Nazca Lines geoglyphs, Nazca desert Peru, pale sand plateau"),
    (("pyramid", "giza", "great pyramid", "khufu"),
     "Great Pyramid of Giza, Giza Plateau Egypt, limestone masonry, "
     "old-kingdom pharaonic era"),
    (("sphinx",),
     "Great Sphinx of Giza, Giza Plateau Egypt, weathered limestone, "
     "pharaonic era"),
    (("baalbek", "trilithon"),
     "Baalbek temple complex Lebanon, thousand-tonne megalithic "
     "trilithon blocks, roman-era platform on pre-roman substructure"),
    (("easter island", "moai", "rapa nui"),
     "Easter Island moai statues, Rapa Nui volcanic tuff, Pacific "
     "coastline Polynesia"),
    (("stonehenge",),
     "Stonehenge stone circle, Salisbury Plain England, sarsen and "
     "bluestone megaliths, neolithic era"),
    (("gobekli", "göbekli", "gobeklitepe"),
     "Göbekli Tepe T-shaped stone pillars, southeastern Turkey, "
     "carved limestone, ~11 000 BCE hunter-gatherer era"),
    (("puma punku", "pumapunku", "tiahuanaco", "tiwanaku"),
     "Puma Punku megalithic site, Bolivian Altiplano, precision-cut "
     "andesite and diorite blocks with H-cut joints"),
    (("atlantis",),
     "Atlantis mythology, submerged ancient civilisation setting"),
    (("tartaria", "tartarian"),
     "Tartaria mythology, grand pre-industrial stone architecture, "
     "eurasian steppe context"),
    (("sumerian", "sumer", "mesopotamia", "ur"),
     "Ancient Sumer / Mesopotamia, mud-brick ziggurats, Tigris-"
     "Euphrates plain, Bronze-Age era"),
    (("angkor", "angkor wat"),
     "Angkor Wat temple complex Cambodia, sandstone Khmer masonry, "
     "surrounding jungle canopy"),
    (("machu picchu",),
     "Machu Picchu Inca citadel, Peruvian Andes, andesite ashlar "
     "masonry, mountain terraces"),
    (("teotihuacan", "teotihuacán"),
     "Teotihuacan Pyramid of the Sun, Avenue of the Dead alignment, "
     "central Mexican highlands, pre-Aztec era"),
    (("dendera", "denderah", "hathor"),
     "Dendera temple complex Egypt, Hathor sanctuary, ptolemaic-era "
     "sandstone reliefs, celestial zodiac ceiling motif"),
    (("dwarka",),
     "Dwarka submerged city ruins, Gulf of Cambay India, sunken "
     "stone masonry"),
    (("yonaguni",),
     "Yonaguni Monument, Ryukyu Islands Japan, submerged terraced "
     "rock formation"),
    (("caral",),
     "Caral pyramid complex, Supe Valley Peru, sunken plazas, "
     "pre-Ceramic Andean era"),
    (("pompeii", "vesuvius"),
     "Pompeii Roman city, Mount Vesuvius volcanic ash, preserved "
     "roman streets and frescoes"),
    (("santorini", "thera", "akrotiri"),
     "Akrotiri Minoan site, Thera / Santorini caldera, volcanic "
     "pumice preservation, Bronze-Age Aegean"),
    # ── Artifact / thematic anchors (identity only, no framing/lighting) ────
    (("crystal skull", "crystal skulls"),
     "carved quartz-crystal skull artefact"),
    (("ark of the covenant", "ark of covenant", "holy ark"),
     "gold-plated Ark of the Covenant artefact, biblical era"),
    (("shroud of turin", "shroud"),
     "Shroud of Turin linen relic with faint human image"),
    (("antikythera", "antikythera mechanism"),
     "Antikythera Mechanism, corroded bronze differential gear "
     "device, hellenistic-era artefact"),
    (("lost technology", "precision finish", "machined", "laser cut"),
     "precision-machined ancient stone or metal artefact with "
     "impossible geometric tolerances for its era"),
    (("oopart", "out of place artefact", "out-of-place artifact", "oopartz"),
     "mysterious out-of-place ancient object, museum-provenance "
     "artefact"),
    (("ancient astronaut", "ancient aliens", "extraterrestrial", "alien contact"),
     "ancient stone reliefs and glyphs depicting humanoid figures "
     "in helmet-like headgear or craft-like vessels"),
    (("lost city", "sunken city", "underwater ruins"),
     "submerged ancient stone city ruins on the ocean floor"),
    (("book of enoch", "enoch", "watchers", "nephilim"),
     "Book of Enoch imagery, ancient manuscript pages and giant "
     "humanoid figures"),
    (("free energy", "tesla", "ancient electricity", "baghdad battery", "vimana"),
     "ancient device suggesting pre-industrial electrical or "
     "advanced technology, museum artefact context"),
    (("dogon", "sirius mystery"),
     "Dogon people of Mali, Bandiagara escarpment, cliff-face "
     "villages, Sirius / Po Tolo cosmological tradition"),
    (("younger dryas", "cataclysm", "comet impact", "black mat"),
     "Younger Dryas impact hypothesis context, permafrost mammoth "
     "fossils, nanodiamond black-mat sediment layer"),
    (("cave painting", "lascaux", "chauvet", "altamira", "prehistoric art"),
     "prehistoric cave-wall pigment paintings, deep limestone "
     "cavern interior, palaeolithic era"),
]

