# -*- coding: utf-8 -*-
"""Pipeline, library, storage, and non-pin formats live in their ADR homes."""
from __future__ import annotations

from pathlib import Path

import agents.media.avatar_engine.visual_architect as shim_architect
import agents.media.b2_budget as shim_budget
import agents.media.b2_client as shim_b2
import agents.media.content_library as shim_library
import agents.orchestrator as shim_orchestrator
import channel_loader as shim_loader
import core.economic_reel_lofi as shim_lofi
import core.page_profile as shim_profile
import core.reference_reel_engine as shim_reference
import core.utils.fingerprint_engine as shim_fingerprint
import engine.bootstrap as shim_bootstrap
import modules.asset_library as shim_assets
import modules.reel_artefact.scene_library as shim_scenes
import production.formats.economic_reel.artefact.scene_library as scenes
import production.formats.lofi as lofi
import production.formats.mei.visual_architect as architect
import production.formats.reference.reference_reel_engine as reference
import production.formats.wealth.fingerprint_engine as fingerprint
import production.library.asset_library as assets
import production.library.content_library as library
import production.pipeline.agentic as orchestrator
import production.pipeline.bootstrap as bootstrap
import production.pipeline.channel_context as loader
import production.pipeline.page_profile as profile
import shared.storage.b2_budget as budget
import shared.storage.b2_client as b2
import utils.quote_renderer as shim_quotes
from production.providers.llm.text_model import writer_primary_model

_REPO = Path(__file__).resolve().parents[1]


def test_moved_modules_are_the_same_objects() -> None:
    assert shim_bootstrap is bootstrap
    assert shim_loader is loader
    assert shim_profile is profile
    assert shim_orchestrator.PipelineOrchestrator is orchestrator.PipelineOrchestrator
    assert shim_b2 is b2
    assert shim_budget is budget
    assert shim_library is library
    assert shim_assets is assets
    assert shim_fingerprint is fingerprint
    assert shim_architect is architect
    assert shim_lofi.run_economic_reel_lofi is lofi.run_economic_reel_lofi


def test_moved_modules_still_resolve_the_repo_root() -> None:
    assert bootstrap.REPO_ROOT == _REPO
    assert loader._ENGINE_ROOT == _REPO
    assert assets._FACTORY_ROOT == _REPO
    assert scenes._FACTORY == _REPO
    assert shim_scenes._FACTORY == _REPO
    assert shim_quotes._ENGINE_ROOT == _REPO
    assert reference._ENGINE_ROOT == _REPO
    assert shim_reference._ENGINE_ROOT == _REPO
    assert architect._ROOT == _REPO


def test_config_facade_stays_cheap_and_rooted_at_the_repo() -> None:
    import config

    assert config.ENGINE_ROOT == _REPO
    assert config.claude_opted_in() is False
    assert config.get_best_claude_model(None) == ""
    assert config.MAX_GOOGLE_COST_PER_RUN_USD == 0.50
    model = writer_primary_model()
    assert model
    assert "claude" not in model.lower()
