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
    wreck, divers, fragments, gears = plan.shots[:4]
    for shot in (wreck, divers):
        assert shot.entity_id == "antikythera_mechanism"
        assert shot.image_role == "flux"
        assert "sea" in shot.canonical_visual.lower()
        assert "gear" not in shot.canonical_visual.lower()
        assert "bce" not in shot.subject.lower()
    for shot in (fragments, gears):
        assert shot.subject == "Antikythera Mechanism"
        assert shot.entity_id == "antikythera_mechanism"
        rendered = render_shot(shot)
        assert rendered.positive.lower().startswith("expected subject: antikythera mechanism.")
        assert "corroded bronze gear fragments in a museum case" in rendered.positive.lower()
        assert "clock face" not in rendered.positive.lower()
        assert "clock face" in rendered.negative.lower()
        assert "bce device" not in rendered.positive.lower()


def test_abstract_close_is_a_flux_safe_scene_not_the_artefact() -> None:
    plan = build_shot_list("Lost OOPArts", _BCE)
    closing = plan.shots[-1]
    rendered = render_shot(closing)
    assert closing.entity_id == "antikythera_mechanism"
    assert closing.image_role == "flux"
    visual = closing.canonical_visual.lower()
    assert "manuscript" in visual or "book" in visual or "archive" in visual
    assert "museum hero" not in closing.camera
    assert "corroded bronze gear fragments" not in rendered.positive.lower()
    assert "readable text" in visual


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
    assert shots[1].image_role == "flux"
    assert "sea" in shots[1].canonical_visual.lower()
    assert shots[2].image_role == "flux"
    assert "museum hero" not in shots[2].camera
    assert "gear" not in shots[2].canonical_visual.lower()


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


def test_commentary_stays_a_documentary_still_and_quimbaya_is_a_pendant() -> None:
    entities = load_entities("ancient_knowledge")
    quimbaya = next(item for item in entities if item.id == "quimbaya_ornaments")
    assert "pendant" in quimbaya.canonical_visual.lower()
    assert "jet" not in quimbaya.canonical_visual.lower()
    shots = normalize_shots(
        [
            _blank(
                "Puma Punku's H-blocks are cut to a precision that still stands.",
                id="s01",
                entity_id="puma_punku",
                subject="Puma Punku",
                canonical_visual="andesite blocks",
                setting="high Andean stone platform",
                camera="wide documentary view",
                shot_type="wide",
            ),
            _blank(
                "Mainstream narratives struggle with these techniques.",
                id="s02",
                entity_id="library",
                subject="a modern library",
                canonical_visual="a modern library with bookshelves and a reading lamp",
                setting="modern library",
                camera="reading lamp over bookshelves",
            ),
            _blank(
                "The Quimbaya ornaments are small gold works from Colombia.",
                id="s03",
                entity_id="quimbaya_ornaments",
                subject="fighter jet",
                canonical_visual="a futuristic gold fighter jet in a sci-fi hangar",
                setting="sci-fi hangar",
                camera="cinematic spaceship",
            ),
            _blank(
                "Historians keep debating what experimental replicas imply.",
                id="s04",
                entity_id="spaceship",
                subject="gold airplane",
                canonical_visual="gold planes floating in a spaceship corridor",
                setting="spaceship corridor",
                camera="futuristic",
            ),
        ],
        entities,
    )
    library = shots[1]
    assert library.entity_id == "puma_punku"
    assert "library" not in library.setting.lower()
    assert "library" not in library.canonical_visual.lower()
    assert "platform" in library.setting.lower() or "site" in library.camera.lower()
    pendant = shots[2]
    assert pendant.entity_id == "quimbaya_ornaments"
    assert "pendant" in pendant.canonical_visual.lower()
    assert "jet" not in pendant.canonical_visual.lower()
    assert "hangar" not in pendant.setting.lower()
    debate = shots[3]
    assert debate.entity_id == "quimbaya_ornaments"
    assert "spaceship" not in debate.setting.lower()
    assert "museum" in debate.setting.lower() or "museum" in debate.camera.lower()
    rendered = render_shot(debate)
    positive = rendered.positive.lower()
    assert "photoreal documentary photograph" in positive
    assert "real camera" in positive
    assert "fighter jet" not in positive
    assert "spaceship" not in positive
    assert "steampunk" in rendered.negative.lower()
    for foreign in ("relationship", "attachment", "therapy", "couple", "wellness"):
        assert foreign not in rendered.negative.lower()


def test_three_debate_lines_do_not_share_one_scene() -> None:
    entities = load_entities("ancient_knowledge")
    shots = normalize_shots(
        [
            _blank(
                "Historians debate the first record.",
                id="s01",
                entity_id="antikythera_mechanism",
                subject="Antikythera Mechanism",
            ),
            _blank(
                "Researchers debate the same record.",
                id="s02",
            ),
            _blank(
                "Scholars debate what the record implies.",
                id="s03",
            ),
        ],
        entities,
    )
    scenes = [shot.scene_type for shot in shots]
    assert all(shot.image_role == "flux" for shot in shots)
    assert scenes[0] == scenes[1]
    assert scenes[2] != scenes[1]
    assert scenes[2]


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
    assert "manuscript" in text.lower() or "old book" in text.lower()
