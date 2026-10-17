# -*- coding: utf-8 -*-
"""Per-topic angle ledger on the existing visual-memory file."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from modules.reel_grounding.config import angle_memory_for
from modules.reel_grounding.memory import (
    opening_hook,
    plan_episode_angle,
    recall,
    remember,
    remember_angle,
)

_POOL = (
    "suppressed evidence — findings institutions will not discuss",
    "temporal impossibility — an object that should not exist in that era",
    "advanced precision — tolerances modern tools barely replicate",
)
_TOPIC = "The London Hammer found in London, Texas"


def test_angle_flag_is_ancient_knowledge_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REEL_ANGLE_MEMORY", raising=False)
    assert angle_memory_for("ancient_knowledge", "investigative") is True
    assert angle_memory_for("ancient_knowledge", "") is True
    assert angle_memory_for("ancient_knowledge", "warrior_discipline") is False
    assert angle_memory_for("master_mei", "investigative") is False
    monkeypatch.setenv("REEL_ANGLE_MEMORY", "0")
    assert angle_memory_for("ancient_knowledge", "investigative") is False


def test_first_episode_locks_an_angle_and_a_revisit_changes_it(tmp_path: Path) -> None:
    first = plan_episode_angle(
        channel="ancient_knowledge",
        topic=_TOPIC,
        pool=list(_POOL),
        root=tmp_path,
    )
    assert first.angle in _POOL
    assert "PRIOR ANGLES" not in first.prompt_block
    assert first.angle in first.prompt_block

    script = (
        "The iron hammer in Cretaceous rock near London, Texas has no agreed date. "
        "Some researchers believe the wooden handle was mineralised in place."
    )
    remember_angle(
        channel="ancient_knowledge",
        topic=_TOPIC,
        angle=first.angle,
        hook=opening_hook(script),
        root=tmp_path,
    )
    # The accepted script recorded once; a second identical commit is a no-op.
    remember_angle(
        channel="ancient_knowledge",
        topic=_TOPIC,
        angle=first.angle,
        hook=opening_hook(script),
        root=tmp_path,
    )
    rows = [
        json.loads(line)
        for line in (tmp_path / "visual_memory.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(rows) == 1
    assert rows[0]["kind"] == "angle"
    assert rows[0]["hook"].startswith("The iron hammer")

    second = plan_episode_angle(
        channel="ancient_knowledge",
        topic="The London Hammer",
        pool=list(_POOL),
        root=tmp_path,
    )
    assert second.angle != first.angle
    assert second.angle in _POOL
    assert "NEW angle" in second.prompt_block
    assert "iron hammer" in second.prompt_block.lower()
    assert first.angle in second.prior_block


def test_visual_recall_ignores_angle_rows(tmp_path: Path) -> None:
    remember_angle(
        channel="ancient_knowledge",
        topic=_TOPIC,
        angle=_POOL[0],
        hook="The iron hammer in Cretaceous rock has no agreed date.",
        root=tmp_path,
    )
    remember(
        channel="ancient_knowledge",
        topic=_TOPIC,
        spoken="The hammer sat in Cretaceous rock.",
        visual_subject="the iron hammer in its rock",
        passed=False,
        rejection="generic urban alley",
        root=tmp_path,
    )
    hints = recall(
        channel="ancient_knowledge",
        topic=_TOPIC,
        spoken="The hammer sat in Cretaceous rock.",
        root=tmp_path,
    )
    assert any("alley" in item for item in hints.rejected)
    assert all("suppressed evidence" not in item for item in hints.rejected)
    assert all("suppressed evidence" not in item for item in hints.accepted)


def test_another_topic_does_not_inherit_the_angle(tmp_path: Path) -> None:
    remember_angle(
        channel="ancient_knowledge",
        topic=_TOPIC,
        angle=_POOL[0],
        hook="The iron hammer in Cretaceous rock has no agreed date.",
        root=tmp_path,
    )
    other = plan_episode_angle(
        channel="ancient_knowledge",
        topic="The Antikythera mechanism",
        pool=list(_POOL),
        root=tmp_path,
    )
    assert "PRIOR ANGLES" not in other.prompt_block
    assert "iron hammer" not in other.prompt_block.lower()


def test_exhausted_pool_asks_for_a_new_hook(tmp_path: Path) -> None:
    for angle in _POOL:
        remember_angle(
            channel="ancient_knowledge",
            topic=_TOPIC,
            angle=angle,
            hook=f"Hook for {angle[:12]}.",
            root=tmp_path,
        )
    plan = plan_episode_angle(
        channel="ancient_knowledge",
        topic=_TOPIC,
        pool=list(_POOL),
        root=tmp_path,
    )
    assert plan.angle == _POOL[0]
    assert "oldest" in plan.prompt_block.lower()
    assert "hook" in plan.prompt_block.lower()


def test_flag_off_does_not_write_or_steer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REEL_ANGLE_MEMORY", "off")
    plan = plan_episode_angle(
        channel="ancient_knowledge",
        topic=_TOPIC,
        pool=list(_POOL),
        root=tmp_path,
    )
    assert plan.angle == ""
    assert plan.prompt_block == ""
    remember_angle(
        channel="ancient_knowledge",
        topic=_TOPIC,
        angle=_POOL[0],
        hook="A hook that must not be stored.",
        root=tmp_path,
    )
    assert not (tmp_path / "visual_memory.jsonl").exists()


def test_opening_hook_uses_the_first_sentence() -> None:
    hook = opening_hook("[ACT 1] The stone should not be here. Later acts add evidence.")
    assert hook == "The stone should not be here."
