# -*- coding: utf-8 -*-
"""Providers, cost, QA, media, and writers live in production/. Old paths are the same objects."""
from __future__ import annotations

from pathlib import Path

import agents.mcp.text_model as shim_text
import agents.media.providers.image_provider as shim_image
import agents.media.providers.model_router as shim_router
import agents.rag.lofi_rag as shim_rag
import agents.writer.caption_engine as shim_captions
import agents.writer.judge_gate as shim_judge
import core.cheap_llm as shim_cheap
import core.spend_ledger as shim_ledger
import models_avatar.service as shim_avatar
import modules.reel_qa as shim_qa_pkg
import modules.reel_qa.local_gate as shim_gate
import production.cost.spend_ledger as ledger
import production.providers.avatar.service as avatar
import production.providers.image.image_provider as image
import production.providers.image.remote_gpu_manager as gpu
import production.providers.llm.cheap_llm as cheap
import production.providers.llm.model_router as router
import production.providers.llm.text_model as text
import production.providers.tts.audio_engine as audio
import production.qa.judges.judge_gate as judge
import production.qa.judges.visual_critic.channel_rag as channel_rag
import production.qa.judges.visual_critic.config as critic_config
import production.qa.local_gate as gate
import production.writers.caption_engine as captions
import production.writers.persona_dna as persona
import production.writers.rag.lofi_rag as rag
import quality.VisualQA_Agent.channel_rag as shim_channel_rag
import quality.VisualQA_Agent.config as shim_critic_config
from production.media.reel_sequence_engine import _ENGINE_ROOT

_REPO = Path(__file__).resolve().parents[1]


def test_production_shims_are_the_same_objects() -> None:
    assert shim_ledger is ledger
    assert shim_ledger.reel_cap_usd is ledger.reel_cap_usd
    assert shim_ledger.SpendCapExceeded is ledger.SpendCapExceeded
    assert shim_cheap is cheap
    assert shim_text is text
    assert shim_text.complete_writer_primary is text.complete_writer_primary
    assert shim_router is router
    assert shim_image is image
    assert shim_gate is gate
    assert shim_judge is judge
    assert shim_captions is captions
    assert shim_rag is rag
    assert shim_avatar is avatar
    assert shim_channel_rag is channel_rag
    assert shim_critic_config is critic_config
    assert shim_qa_pkg.seal_compiled_reel.__module__ == "production.qa.pipeline"
    profile = _REPO / "production" / "qa" / "judges" / "profiles" / "ancient_mystery.json"
    assert profile.is_file()


def test_moved_modules_still_resolve_the_repo_root() -> None:
    assert _ENGINE_ROOT == _REPO
    assert persona._ENGINE_ROOT == _REPO
    assert critic_config.PROJECT_ROOT == _REPO
    assert critic_config.FACTORY_ROOT == _REPO
    assert Path(gpu.__file__).resolve().parents[3] == _REPO
    assert Path(audio.__file__).resolve().parents[3] == _REPO
    assert Path(text.__file__).resolve().parents[3] == _REPO


def test_writer_primary_stays_on_the_cheap_stack() -> None:
    dna = _REPO / "agents" / "media" / "master_dna.json"
    assert dna.is_file()
    model = text.writer_primary_model()
    assert model
    assert "claude" not in model.lower()
    assert "latest" not in model.lower()
    assert text.complete_writer_primary is not text.complete_script
