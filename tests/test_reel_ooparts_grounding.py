# -*- coding: utf-8 -*-
"""Offline checks for the Lost OOPArts stills. No image API calls."""
from __future__ import annotations

from core.audio_chunker import chunk_word_timings
from modules.reel_grounding.prompt import (
    assemble_beat,
    format_resolved_objects,
    ground_image_prompt,
)
from modules.reel_qa.beats import pack_sentences
from modules.reel_qa.prompts import refine_prompt

_TOPIC = "Lost OOPArts"
_SCRIPT = (
    "In 1961 the Coso artifact was found inside a geode in California. "
    "It is a small manufactured object sealed in rock. "
    "Some believe it's a complex natural geological formation. "
    "People compare the shape to a statue. "
    "The technology looks centuries ahead of its time. "
    "The machined grooves line up with the shape of an ancient spark plug perfectly. "
    "The Coso artifact isn't unique in timeline anomalies. "
    "The Klerksdorp spheres show grooves that look machined."
)
_THEORY = "Some believe it's a complex natural geological formation."
_SPHERES = "The Klerksdorp spheres show grooves that look machined."
_FRAGMENT = "perfectly. The Coso artifact isn't unique in timeline"
_STATUE = "People compare the shape to a statue."
_TECH = "The technology looks centuries ahead of its time."


def _ground(spoken: str, **kwargs: object) -> str:
    return ground_image_prompt(
        topic=_TOPIC,
        script=_SCRIPT,
        spoken=spoken,
        draft=(
            "SUBJECT — WIDE LANDSCAPE / RUINS: torchlit megaliths and a "
            "cyberpunk figure beside a cannon."
        ),
        shot_text="Wide establishing shot — horizon visible, landscape dominating.",
        lighting_text="Torch / firelight lit from BELOW the subject.",
        channel="ancient_knowledge",
        domain_anchors="marble architecture, bronze patina, temple ruins",
        **kwargs,  # type: ignore[arg-type]
    )


def _primary(prompt: str) -> str:
    return prompt.lower().split("this beat's object:")[0]


def test_theory_line_shows_the_coso_artifact_as_a_concretion() -> None:
    prompt = _ground(_THEORY)
    primary = _primary(prompt)
    low = prompt.lower()
    assert low.startswith("primary subject:")
    assert "coso artifact" in primary
    assert "concretion" in primary or "geode" in primary
    assert "klerksdorp" not in primary
    assert "cannon" not in primary
    assert "cyberpunk" not in primary
    assert "monument" not in primary
    assert "stay out of frame" not in low
    assert "california" not in low
    plan = assemble_beat(
        topic=_TOPIC, script=_SCRIPT, spoken=_THEORY, channel="ancient_knowledge",
    )
    neg = plan.negative.lower()
    assert "cannon" in neg
    assert "klerksdorp spheres" in neg
    assert "klerksdorp" not in plan.positive.lower()


def test_spheres_beat_does_not_show_the_coso_artifact() -> None:
    prompt = _ground(_SPHERES)
    primary = _primary(prompt)
    low = prompt.lower()
    assert "klerksdorp spheres" in primary
    assert "coso" not in low
    assert "california" not in low
    neg = assemble_beat(
        topic=_TOPIC, script=_SCRIPT, spoken=_SPHERES, channel="ancient_knowledge",
    ).negative.lower()
    assert "coso artifact" in neg


def test_mid_sentence_fragment_keeps_the_named_object() -> None:
    prompt = _ground(
        _FRAGMENT,
        previous_spoken=(
            "The machined grooves line up with the shape of an ancient "
            "spark plug perfectly."
        ),
        next_spoken=_SPHERES,
    )
    primary = _primary(prompt)
    assert "coso artifact" in primary
    assert "perfectly" not in primary
    assert "monument" not in primary
    assert "klerksdorp" not in primary


def test_fragment_without_a_name_inherits_the_neighbour() -> None:
    prompt = _ground(
        "perfectly.",
        previous_spoken="The grooves on the Coso artifact line up.",
        next_spoken=_SPHERES,
    )
    primary = _primary(prompt)
    assert "coso artifact" in primary
    assert "klerksdorp" not in primary


def test_statue_and_technology_lines_stay_on_the_artifact() -> None:
    statue = _primary(_ground(_STATUE))
    tech = _primary(_ground(_TECH))
    assert "coso artifact" in statue
    assert "statue" not in statue
    assert "coso artifact" in tech
    assert "cyberpunk" not in tech
    assert "cyberpunk" in assemble_beat(
        topic=_TOPIC, script=_SCRIPT, spoken=_TECH, channel="ancient_knowledge",
    ).negative.lower()


def test_small_object_beats_drop_landmark_anchors() -> None:
    low = _ground(_THEORY).lower()
    assert "marble" not in low
    assert "temple" not in low
    assert "bronze patina" in low
    assert "pyramid" not in low
    assert "cannon" not in low
    assert "clock face" not in low
    assert "vault door" not in low


def test_retry_locks_the_beat_object_not_the_other_artefact() -> None:
    grounded = _ground(_THEORY)
    refined = refine_prompt(
        grounded,
        _THEORY,
        episode_subject=_TOPIC,
        rejection_reason="judge saw a cannon",
    )
    low = refined.lower()
    assert "coso artifact" in low
    assert "concretion" in low or "geode" in low
    assert "klerksdorp" not in low
    assert "cannon" not in low.split("primary subject:", 1)[1].split("this beat")[0]


_FOUR_BEATS = [
    "The Antikythera Mechanism came out of a shipwreck.",
    "Corroded fragments sat in a museum drawer for decades.",
    "In 1961 the Coso artifact was found inside a geode.",
    "Some believe it's a complex natural geological formation.",
    "The technology looks centuries ahead of its time.",
    "People compare the shape to a statue.",
    "The Coso artifact isn't unique.",
    "The Serapeum holds granite boxes cut from a single block.",
    "Each box is larger than a bull and perfectly squared.",
    "The lids alone weigh many tons.",
    "Colombia produced golden jet planes centuries ago.",
    "The ornaments have wings, tails, and a cockpit.",
    "They were filed as animals until the shape was obvious.",
]
_FOUR_SCRIPT = " ".join(_FOUR_BEATS)
_EGYPT_BANS = ["pyramid", "pyramids", "egyptian", "egypt", "pharaoh", "sphinx", "giza"]


def test_four_object_episode_switches_subject_per_segment() -> None:
    report = format_resolved_objects(
        _TOPIC, _FOUR_SCRIPT, _FOUR_BEATS, banned=_EGYPT_BANS,
    )
    print(report)
    rows = report.splitlines()
    assert len(rows) == 13
    assert all("Antikythera Mechanism" in rows[i] for i in (0, 1))
    assert all("Coso artifact" in rows[i] for i in range(2, 7))
    assert all("Serapeum granite boxes" in rows[i] for i in range(7, 10))
    assert all("Colombian golden jet planes" in rows[i] for i in range(10, 13))
    # Neighbour text about the Coso artifact must not override the new subject.
    switched = assemble_beat(
        topic=_TOPIC,
        script=_FOUR_SCRIPT,
        spoken=_FOUR_BEATS[7],
        previous_spoken=_FOUR_BEATS[6],
        next_spoken=_FOUR_BEATS[8],
        banned=_EGYPT_BANS,
        negative=", ".join(_EGYPT_BANS),
        domain_anchors="marble architecture, clockwork gears, bronze patina",
    )
    positive = switched.positive.lower()
    assert switched.object_label == "the Serapeum granite boxes"
    assert "coso" not in positive
    assert "granite boxes" in positive
    assert "stay out of frame" not in positive
    assert "cannon" not in positive
    assert "classical statue" not in positive
    assert "clock face" not in positive
    assert "vault door" not in positive
    for token in _EGYPT_BANS:
        assert token not in switched.banned
        assert token not in switched.negative.lower()
    assert "cannon" in switched.negative.lower()
    assert "coso artifact" in switched.negative.lower()

    gears = assemble_beat(
        topic=_TOPIC,
        script=_FOUR_SCRIPT,
        spoken=_FOUR_BEATS[0],
        next_spoken=_FOUR_BEATS[1],
        banned=_EGYPT_BANS,
        domain_anchors="clockwork gears, marble architecture, bronze patina",
    )
    gear_pos = gears.positive.lower()
    assert "corroded bronze gear fragments" in gear_pos
    assert "museum case" in gear_pos
    assert "clockwork" not in gear_pos
    assert "clock face" not in gear_pos
    assert "vault door" not in gear_pos
    assert "clock face" in gears.negative.lower()
    assert "vault door" in gears.negative.lower()
    assert "egypt" in gears.banned


def test_sentence_final_word_stays_on_its_beat() -> None:
    spoken = (
        "The machined grooves line up with the shape of an ancient spark "
        "plug perfectly. The Coso artifact isn't unique in timeline."
    )
    words: list[tuple[str, float, float]] = []
    t = 0.0
    for word in spoken.split():
        words.append((word, t, t + 0.36))
        t += 0.38
    chunks = chunk_word_timings(words)
    assert chunks
    assert all(chunk.duration_s <= 5.6 for chunk in chunks)
    assert any(chunk.text.endswith("perfectly.") for chunk in chunks)
    assert all(not chunk.text.startswith("perfectly.") for chunk in chunks)
    follow = [chunk.text for chunk in chunks if "Coso" in chunk.text]
    assert follow and follow[0].startswith("The Coso")


def test_sentence_packer_does_not_leave_the_tail_on_the_next_beat() -> None:
    text = (
        "The machined grooves line up with the shape of an ancient spark "
        "plug perfectly. The Coso artifact isn't unique in timeline anomalies."
    )
    groups = pack_sentences(text, 8.0, min_s=3.0, max_s=5.0)
    assert groups[0][0].endswith("perfectly.")
    assert groups[1][0].startswith("The Coso")
