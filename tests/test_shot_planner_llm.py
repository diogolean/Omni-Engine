# -*- coding: utf-8 -*-
"""Planner call, one JSON repair, and the resolver fallback. No image calls."""
from __future__ import annotations

import json

import agents.orchestrator.llm as llm
from core.cost_tracker import CostTracker
from modules.reel_grounding.prompt import assemble_beat
from modules.reel_qa.config import RELEVANCE_THRESHOLD
from modules.shot_planner.entities import load_entities
from modules.shot_planner.plan import (
    plan_episode,
    plan_for_reel,
    planner_enabled,
    planner_prompt,
    shot_for_beat,
)
from modules.shot_planner.render import render_shot

_BCE = (
    "The Antikythera Mechanism came out of a shipwreck. "
    "Divers found a complex 2nd century BCE device. "
    "Corroded fragments sat in a museum drawer for decades. "
    "The gears are cut with a precision that still surprises researchers. "
    "OOPArts unlock profound questions about our world."
)
_SENTENCES = [part.strip() for part in _BCE.split(". ") ]
_SENTENCES = [part if part.endswith(".") else part + "." for part in _SENTENCES]


def _bad_model_json() -> str:
    shots = []
    for index, sentence in enumerate(_SENTENCES, start=1):
        if index == 2:
            entity_id, subject, visual = "bce_device", "BCE device", "a strange device"
        elif index == len(_SENTENCES):
            entity_id, subject, visual = "ooparts", "OOPArts", "a vague cosmic vista"
        else:
            entity_id, subject = "antikythera_mechanism", "Antikythera Mechanism"
            visual = "wrong paraphrase"
        shots.append({
            "id": f"s{index:02d}",
            "narration": sentence,
            "entity_id": entity_id,
            "subject": subject,
            "canonical_visual": visual,
            "shot_type": "macro",
            "camera": "close",
            "setting": "museum",
            "era": "Hellenistic",
            "mood": "quiet, investigative",
            "lighting": "single raking museum lamp",
            "negatives": [],
        })
    return json.dumps({
        "channel": "ancient_knowledge",
        "topic": "Lost OOPArts",
        "narration": _BCE,
        "shots": shots,
    })


def test_flag_defaults_off_and_is_ancient_knowledge_only(monkeypatch) -> None:
    monkeypatch.delenv("REEL_SHOT_PLANNER", raising=False)
    assert planner_enabled("ancient_knowledge") is False

    def boom(prompt: str, system: str | None = None) -> str:
        raise AssertionError(prompt or system)

    assert plan_for_reel(
        channel="ancient_knowledge", topic="Lost OOPArts", script=_BCE, complete=boom,
    ) is None
    monkeypatch.setenv("REEL_SHOT_PLANNER", "1")
    assert planner_enabled("ancient_knowledge") is True
    assert planner_enabled("wonder_feed") is False
    assert plan_for_reel(
        channel="wonder_feed", topic="Lost OOPArts", script=_BCE, complete=boom,
    ) is None


def test_prompt_forbids_era_generic_entities_and_asks_for_a_hero_shot() -> None:
    text = planner_prompt("Lost OOPArts", _BCE, load_entities("ancient_knowledge"))
    assert "2nd century BCE device" in text
    assert "flux-safe" in text.lower() or "manuscript" in text.lower()
    assert "never a named artefact" in text.lower() or "its gears" in text.lower()
    assert "antikythera_mechanism" in text
    assert "small gold pendants" in text
    assert "never a modern jet" in text
    assert "steampunk" in text


def test_invalid_json_is_retried_once_then_accepted() -> None:
    calls = {"n": 0}
    good = _bad_model_json()

    def complete(prompt: str, system: str | None = None) -> str:
        del prompt, system
        calls["n"] += 1
        return "not json" if calls["n"] == 1 else good

    plan = plan_episode("Lost OOPArts", _BCE, complete=complete)
    assert calls["n"] == 2
    assert plan is not None
    assert plan.shots[1].entity_id == "antikythera_mechanism"
    assert "bce" not in plan.shots[1].subject.lower()
    closing = plan.shots[-1]
    assert closing.entity_id == "antikythera_mechanism"
    assert closing.image_role == "flux"
    assert "museum hero" not in closing.camera
    rendered = render_shot(closing)
    assert "corroded bronze gear fragments" not in rendered.positive.lower()
    assert "vague cosmic" not in rendered.positive.lower()
    assert "manuscript" not in rendered.positive.lower()
    assert closing.scene_type in {
        "landscape", "sea", "aerial", "detail", "golden", "fog", "ruins", "coast",
        "establishing", "atmosphere",
    }
    matched = shot_for_beat(plan, _SENTENCES[1])
    assert matched is not None and matched.entity_id == "antikythera_mechanism"


def test_two_invalid_replies_fall_back_to_the_resolver() -> None:
    calls = {"n": 0}

    def complete(prompt: str, system: str | None = None) -> str:
        del prompt, system
        calls["n"] += 1
        return "still not json"

    assert plan_episode("Lost OOPArts", _BCE, complete=complete) is None
    assert calls["n"] == 2
    spoken = "Divers found a complex 2nd century BCE device."
    resolved = assemble_beat(
        topic="Lost OOPArts", script=_BCE, spoken=spoken, channel="ancient_knowledge",
    )
    assert "antikythera" in resolved.object_label.lower()
    assert "bce" not in resolved.object_label.lower()
    close = assemble_beat(
        topic="Lost OOPArts",
        script=_BCE,
        spoken="OOPArts unlock profound questions about our world.",
        channel="ancient_knowledge",
    )
    assert close.object_label == "the Antikythera Mechanism"
    assert "museum hero shot" in close.positive.lower()
    assert shot_for_beat(None, spoken) is None


def test_cost_tracker_is_bound_for_the_call(monkeypatch) -> None:
    seen: list[object] = []
    monkeypatch.setattr(llm, "set_cost_tracker", lambda tracker: seen.append(tracker))
    tracker = CostTracker(page_id="ancient_knowledge")
    plan_episode(
        "Lost OOPArts", _BCE, complete=lambda prompt, system=None: _bad_model_json(),
        tracker=tracker,
    )
    assert seen[0] is tracker
    assert seen[-1] is None


def test_relevance_threshold_stays_at_070() -> None:
    assert RELEVANCE_THRESHOLD == 0.70


def test_visible_warning_says_whether_the_planner_was_used(monkeypatch, caplog) -> None:
    import logging

    monkeypatch.setenv("REEL_SHOT_PLANNER", "1")
    with caplog.at_level(logging.WARNING):
        plan = plan_for_reel(
            channel="ancient_knowledge",
            topic="Lost OOPArts",
            script=_BCE,
            complete=lambda prompt, system=None: _bad_model_json(),
        )
    assert plan is not None
    assert "[ShotPlanner] used planner (" in caplog.text
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        missed = plan_for_reel(
            channel="ancient_knowledge",
            topic="Lost OOPArts",
            script=_BCE,
            complete=lambda prompt, system=None: "still not json",
        )
    assert missed is None
    assert "[ShotPlanner] fell back to resolver:" in caplog.text


def test_planner_cost_line_uses_the_reel_channel(caplog) -> None:
    import logging

    from core.google_guardrail import GoogleCostTracker, bind_cost_channel

    seen: list[str | None] = []

    def _record(channel: str | None) -> None:
        seen.append(channel)

    # The planner binds the reel channel around the Gemini call, then clears it.
    import core.google_guardrail as guard

    original = guard.bind_cost_channel
    guard.bind_cost_channel = _record  # type: ignore[method-assign]
    try:
        plan_episode(
            "Lost OOPArts",
            _BCE,
            complete=lambda prompt, system=None: _bad_model_json(),
        )
    finally:
        guard.bind_cost_channel = original
    assert seen[0] == "ancient_knowledge"
    assert seen[-1] is None

    bind_cost_channel("ancient_knowledge")
    try:
        tracker = GoogleCostTracker()
        with caplog.at_level(logging.INFO, logger="production.cost.google_guardrail"):
            tracker.record(
                model="models/gemini-2.5-flash",
                kind="text",
                input_tokens=20,
                output_tokens=20,
                source="shot_planner",
            )
        assert any(entry.channel == "ancient_knowledge" for entry in tracker._entries)
    finally:
        bind_cost_channel(None)
    assert "channel=ancient_knowledge" in caplog.text
