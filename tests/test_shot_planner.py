# -*- coding: utf-8 -*-
"""Offline shot-list checks. No image calls and no model calls."""
from __future__ import annotations

import re

from modules.shot_planner.build import build_shot_list
from modules.shot_planner.entities import is_era_generic, load_entities
from modules.shot_planner.normalize import normalize_shots
from modules.shot_planner.render import render_shot
from modules.shot_planner.report import render_report
from modules.shot_planner.schema import Shot, validate_shot_list

_BCE = (
    "The Antikythera Mechanism came out of a shipwreck. "
    "Divers found a complex 2nd century BCE device. "
    "Corroded fragments sat in a museum drawer for decades. "
    "The gears are cut with a precision that still surprises researchers. "
    "OOPArts unlock profound questions about our world."
)
_FOUR = (
    "The Antikythera Mechanism came out of a shipwreck. "
    "In 1961 the Coso artifact was found inside a geode. "
    "Some believe it's a complex natural geological formation. "
    "The Serapeum holds granite boxes cut from a single block. "
    "Each box is larger than a bull. "
    "Colombia produced golden jet planes centuries ago."
)


def _blank(narration: str, **overrides: object) -> Shot:
    data = dict(
        id="s00",
        narration=narration,
        entity_id="",
        subject="",
        canonical_visual="",
        shot_type="",
        camera="",
        setting="",
        era="",
        mood="",
        lighting="",
        negatives=[],
        kb_miss=False,
    )
    data.update(overrides)
    return Shot(**data)  # type: ignore[arg-type]


def test_era_words_plus_a_generic_noun_are_not_an_entity() -> None:
    assert is_era_generic("2nd century BCE device")
    assert is_era_generic("the BCE device")
    assert not is_era_generic("Antikythera Mechanism")
    assert not is_era_generic("Coso artifact")
    plan = build_shot_list("Lost OOPArts", _BCE)
    assert [shot.subject for shot in plan.shots[:4]] == ["Antikythera Mechanism"] * 4
    for shot in plan.shots[:4]:
        assert shot.entity_id == "antikythera_mechanism"
        assert "bce" not in shot.subject.lower()
        rendered = render_shot(shot)
        assert rendered.positive.lower().startswith("expected subject: antikythera mechanism.")
        assert "corroded bronze gear fragments in a museum case" in rendered.positive.lower()
        assert "clock face" not in rendered.positive.lower()
        assert "clock face" in rendered.negative.lower()
        assert "bce device" not in rendered.positive.lower()


def test_abstract_close_is_a_museum_hero_of_the_main_artefact() -> None:
    plan = build_shot_list("Lost OOPArts", _BCE)
    closing = plan.shots[-1]
    rendered = render_shot(closing)
    assert closing.subject == "Antikythera Mechanism"
    assert closing.entity_id == "antikythera_mechanism"
    assert "museum hero shot" in closing.camera
    assert "museum hero shot" in rendered.positive.lower()
    assert "corroded bronze gear fragments" in rendered.positive.lower()
    assert rendered.positive.lower().index("antikythera") < 400
    assert rendered.subject == "Antikythera Mechanism"


def test_normalize_rewrites_a_model_invented_bce_device_and_a_vague_close() -> None:
    entities = load_entities("ancient_knowledge")
    shots = normalize_shots(
        [
            _blank(
                "The Antikythera Mechanism came out of a shipwreck.",
                id="s01",
                entity_id="antikythera_mechanism",
                subject="Antikythera Mechanism",
            ),
            _blank(
                "Divers found a complex 2nd century BCE device.",
                id="s02",
                entity_id="bce_device",
                subject="BCE device",
            ),
            _blank(
                "OOPArts unlock profound questions about our world.",
                id="s03",
                entity_id="ooparts",
                subject="OOPArts",
            ),
        ],
        entities,
    )
    assert shots[1].entity_id == "antikythera_mechanism"
    assert "bce" not in shots[1].subject.lower()
    assert shots[2].subject == "Antikythera Mechanism"
    assert "museum hero shot" in shots[2].camera


def test_four_object_episode_switches_subject_per_segment() -> None:
    plan = build_shot_list("Lost OOPArts", _FOUR)
    subjects = [shot.entity_id for shot in plan.shots]
    assert subjects == [
        "antikythera_mechanism",
        "coso_artifact",
        "coso_artifact",
        "serapeum_boxes",
        "serapeum_boxes",
        "quimbaya_ornaments",
    ]
    geology = plan.shots[2]
    assert "concretion" in geology.canonical_visual or "geode" in geology.canonical_visual
    assert geology.entity_id == "coso_artifact"
    serapeum = render_shot(plan.shots[3])
    assert "underground stone gallery" in serapeum.positive.lower()
    assert "doorway" not in serapeum.positive.lower()
    assert "coso" not in serapeum.positive.lower()


def test_a_pyramid_episode_keeps_the_pyramid() -> None:
    plan = build_shot_list(
        "Great Pyramid muon chambers",
        "Muon scans of the Great Pyramid found a void above the Grand Gallery.",
    )
    shot = plan.shots[0]
    rendered = render_shot(shot)
    assert shot.entity_id == "great_pyramid"
    assert "great pyramid" in rendered.positive.lower()
    assert not re.search(r"\bpyramid\b", rendered.negative.lower())
    forced = render_shot(Shot(
        id=shot.id,
        narration=shot.narration,
        entity_id=shot.entity_id,
        subject=shot.subject,
        canonical_visual=shot.canonical_visual,
        shot_type=shot.shot_type,
        camera=shot.camera,
        setting=shot.setting,
        era=shot.era,
        mood=shot.mood,
        lighting=shot.lighting,
        negatives=shot.negatives + ["pyramid", "pyramids"],
    ))
    assert not re.search(r"\bpyramid\b", forced.negative.lower())


def test_schema_rejects_a_shot_with_no_visual() -> None:
    plan = build_shot_list("Lost OOPArts", _BCE)
    raw = plan.to_dict()
    assert validate_shot_list(raw) == []
    raw["shots"][0]["canonical_visual"] = ""
    errors = validate_shot_list(raw)
    assert any("canonical_visual" in error for error in errors)


def test_offline_report_prints_the_shot_list_and_the_prompts() -> None:
    text = render_report("Lost OOPArts", _BCE)
    assert "no image call, no model call" in text
    assert "EXPECTED SUBJECT: Antikythera Mechanism." in text
    assert "NEGATIVE:" in text
    assert "EXPECTED SUBJECT: BCE device" not in text
    assert '"subject": "BCE device"' not in text
    assert "museum hero shot" in text
