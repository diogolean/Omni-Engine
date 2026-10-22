# -*- coding: utf-8 -*-
"""Process settings facade.

The bodies live in three homes (ADR 0008):

* env bootstrap — ``shared.env.load_factory_dotenv``
* model and key constants — ``production.providers.settings``
* prices and the Google cap — ``production.cost.pricing``
* page paths — ``shared.paths.resolve_page_layout``

``import config`` is still the module callers read and tests patch. Entrypoints
import this module first so ``.env`` is loaded with ``override=True`` before
those constants are read. The default model tier stays cheap. Claude is opt-in.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from shared.env import load_factory_dotenv as _load_factory_dotenv
from shared.paths import (
    assets_root,
    channel_store_dir,
    drive_root as _drive_root,
    factory_root as _factory_root,
    outputs_root,
    page_assets_dir,
    page_outputs_dir,
    resolve_page_layout as _resolve_page_layout,
)

logger = logging.getLogger(__name__)

ENGINE_ROOT: Path = _factory_root()
DOTENV_PATH: Path = ENGINE_ROOT / ".env"


def _load_project_dotenv() -> tuple[Path, bool]:
    return _load_factory_dotenv(DOTENV_PATH)


_DOTENV_RESOLVED_PATH, DOTENV_LOADED_FROM_FILE = _load_project_dotenv()


def print_dotenv_bootstrap() -> None:
    if DOTENV_LOADED_FROM_FILE:
        print(f"[bootstrap] .env loaded: {_DOTENV_RESOLVED_PATH}")
    else:
        print(f"[bootstrap] .env not loaded from {_DOTENV_RESOLVED_PATH}")


# Constants read os.environ, so they import only after the file load above.
from production.cost import pricing as _pricing
from production.providers import settings as _settings


def _export(module: object) -> None:
    for key, value in vars(module).items():
        if key.startswith("__") or key in {"_facade", "logger", "sys"}:
            continue
        globals()[key] = value


_export(_settings)
_export(_pricing)
del _export, _settings, _pricing

for _key, _value in _resolve_page_layout(ENGINE_ROOT).items():
    globals()[_key] = _value
del _key, _value

# Cost-first router bootstrap (cheap unless USE_PREMIUM_MODEL / MODEL_TIER).
# Mutations land on this module, which is what callers read.
try:
    from production.providers.llm.model_router import (
        resolve_tier as _resolve_model_tier,
        route_model,
        sync_config_defaults as _sync_model_router_defaults,
    )

    _sync_model_router_defaults()
    if _resolve_model_tier() == "premium":
        IMAGE_MODEL_FALLBACK_CHAIN = [
            PREMIUM_IMAGE_MODEL,
            SAFE_GEMINI_IMAGE_MODEL,
            SAFE_GEMINI_IMAGE_FALLBACK_2,
        ]
        if not os.getenv("GEMINI_IMAGE_MODEL"):
            GEMINI_IMAGE_MODEL = normalize_image_model_id(PREMIUM_IMAGE_MODEL)
        if not os.getenv("GEMINI_ECONOMIC_BRAIN_MODEL"):
            GEMINI_ECONOMIC_BRAIN_MODEL = PREMIUM_TEXT_MODEL
        if not os.getenv("GEMINI_RESEARCH_MODEL"):
            GEMINI_RESEARCH_MODEL = PREMIUM_TEXT_MODEL
except Exception as _router_exc:  # noqa: BLE001
    logger.debug("Model router bootstrap skipped (%s)", _router_exc)


def bind_active_page(page: str) -> str:
    """Rebind page-namespaced output paths after a late ``--channel`` resolve.

    ``config`` may have been imported before ``sys.argv`` was rewritten
    (E2E harnesses, CaptionEngine preloads). Call this once the CLI channel
    is known so clips land under ``{OUTPUT_PATH}/{page}/clips``.
    """
    layout = _resolve_page_layout(ENGINE_ROOT, page=page, rebind=True)
    for key, value in layout.items():
        globals()[key] = value
    logger.info(
        "ACTIVE_PAGE bound | page=%s outputs=%s clips=%s",
        layout["ACTIVE_PAGE"],
        layout["PAGE_OUTPUTS_DIR"],
        Path(layout["PAGE_OUTPUTS_DIR"]) / "clips",
    )
    try:
        from core.google_guardrail import bind_run_channel

        bind_run_channel(str(layout["ACTIVE_PAGE"]))
    except Exception:
        logger.debug("Google cost channel not bound.", exc_info=True)
    return str(layout["ACTIVE_PAGE"])


def reference_avatar_resolved_path() -> Path:
    return Path(REFERENCE_IMAGE_PATH).resolve()


def reference_avatar_exists() -> bool:
    return Path(REFERENCE_IMAGE_PATH).is_file()


def warn_if_reference_avatar_missing() -> None:
    if reference_avatar_exists():
        return
    logger.warning(
        "Reference likeness file not found at %s. Image generation falls back to text-only prompting.",
        reference_avatar_resolved_path(),
    )
