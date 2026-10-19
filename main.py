# -*- coding: utf-8 -*-
"""CLI entry for the content factory.

UTF-8 setup, Drive-text repair, ``--page`` pre-parse, and ``.env`` stay here
so they run before ``config`` and the channel packages import. The steps
themselves live in ``engine/``.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# Force UTF-8 for the whole process BEFORE any print / path / import side effects.
# Windows defaults to cp1252 for console I/O, which corrupts accented filenames
# (e.g. gobekli) and crashes on arrows/em dashes in logs.
os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
if sys.platform == "win32":
    try:
        import ctypes as _ctypes

        _ctypes.windll.kernel32.SetConsoleCP(65001)
        _ctypes.windll.kernel32.SetConsoleOutputCP(65001)
    except Exception:
        pass

from engine.bootstrap import (  # noqa: E402
    REPO_ROOT,
    _clean_all_python_sources,
    _preparse_active_page,
)

_ENGINE_ROOT_BOOT = REPO_ROOT
_clean_all_python_sources(_ENGINE_ROOT_BOOT)
_PRELOADED_PAGE = _preparse_active_page()

from dotenv import load_dotenv as _load_dotenv  # noqa: E402

_load_dotenv(REPO_ROOT / ".env", override=True, encoding="utf-8-sig")
# .env must not clobber the Phase-0 CLI channel (``--channel`` / ``--page``).
_PRELOADED_PAGE = _preparse_active_page()

from engine.log import bind_logger  # noqa: E402

_LOG = logging.getLogger(__name__)
bind_logger(_LOG)

# Heavy imports and the ACTIVE_PAGE rebind. Must stay after the pre-parse.
import engine.runtime as _runtime  # noqa: E402

for _name in dir(_runtime):
    if _name.startswith("_") and _name not in {
        "_GEO_ANCHORS",
        "_LOGO_ONLY_STILL_POST_TYPES",
        "_MEI_BAN_PLAIN_MODERN",
        "_MEI_DNA_FALLBACK",
        "_MEI_PHOTOREAL",
        "_assert_mei_avatar",
        "_classify_mei_visual_theme",
        "_mei_build_act_prompts",
        "_re",
        "_resolve_mei_avatar_path",
        "_MAX_AUDIO_DURATION_RETRIES",
        "_MIN_CACHED_STILL_BYTES",
        "_SEQUENCE_VIDEO_POST_TYPES",
        "_VOICE_MAX_OBSERVED_WPS",
        "_VOICE_MIN_OBSERVED_WPS",
        "_VOICE_TOLERANCE_FRACTION",
    }:
        continue
    if _name.startswith("__"):
        continue
    globals().setdefault(_name, getattr(_runtime, _name))

from engine.acts import (  # noqa: E402
    _is_sequence_video_post,
    _planner_media_url,
    _is_logo_only_still,
    _load_engagement_bait_examples,
    _silence_noisy_http_loggers,
    _looks_like_upstream_api_failure,
    _extract_topic_visual_entities,
    _strip_audio_behavior_tags,
    _split_script_into_act_chunks,
    _build_master_mei_script_act_prompts,
    _build_reel_act_descriptors,
)
from engine.stills import (  # noqa: E402
    _export_scene_assets_sequential,
    _ensure_sequence_image,
    _is_valid_cached_still,
    _sequence_cache_search_roots,
    _score_cached_still,
    _stem_act_globs,
    _find_cached_sequence_still,
    _collect_cached_act_stills,
    _materialize_cached_still,
    _apply_still_cache_to_jobs,
    _track_adapter_image,
    _track_remote_gpu_batch,
    _image_act_max_workers,
    _run_acts_parallel,
    _maybe_convert_to_video,
)
from engine.reporting import (  # noqa: E402
    _color,
    _emit_clean_api_error,
    _format_cost_block,
    _print_production_summary,
    _snapshot_verified_models,
    _preflight_gemini_image_batch_cost,
    _bootstrap_pipeline_intro,
    _print_video_cost_summary,
    _print_test_footer,
)
from engine.voice import (  # noqa: E402
    _trim_script_to_word_limit,
    _filter_audio_tag_timings,
    _stitch_audio_sequential,
    _commit_reel_angle,
    _synthesize_sequence_voice_track,
    _reel_audio_is_f5,
)
from engine.orchestrator import (  # noqa: E402
    _produce_variant_worker,
    _produce_agentic,
    _persist_agentic_postplanner,
    produce,
    run_pipeline,
    run_test_images_debug_mode,
)
from engine.cli import (  # noqa: E402
    _together_cli_image_model,
    cli,
)
from engine.bootstrap import (  # noqa: E402
    _clean_all_python_sources,
    _preparse_active_page,
    _repair_drive_text_file,
)


if __name__ == "__main__":
    cli()
