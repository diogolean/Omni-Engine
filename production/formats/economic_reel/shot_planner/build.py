# -*- coding: utf-8 -*-
"""Build a shot list from a script and the entity file. No model call."""
from __future__ import annotations

from shared.channel_flags import sole_page

import re

from production.formats.economic_reel.shot_planner.entities import entities_in_text, is_abstract_close, load_entities
from production.formats.economic_reel.shot_planner.normalize import normalize_shots
from production.formats.economic_reel.shot_planner.schema import Shot, ShotList

_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def split_sentences(text: str) -> list[str]:
    parts = [part.strip() for part in _SENTENCE.split(text or "") if part.strip()]
    if parts:
        return parts
    text = " ".join((text or "").split())
    return [text] if text else []


def build_shot_list(
    topic: str,
    script: str,
    *,
    channel: str = sole_page("DOCUMENTARY_PIPELINE"),
) -> ShotList:
    """One shot per sentence. Entities come from the channel knowledge base.

    A sentence that only says ``2nd century BCE device`` keeps the artefact
    already introduced. A closing line with no object becomes a museum hero
    shot of the episode's main artefact.
    """
    entities = load_entities(channel)
    topic_hits = entities_in_text(topic, entities)
    topic_entity = topic_hits[0] if topic_hits else None
    sentences = split_sentences(script) or split_sentences(topic)
    shots: list[Shot] = []
    seen_named = False
    for index, sentence in enumerate(sentences, start=1):
        named = entities_in_text(sentence, entities)
        if named:
            entity_id = named[0].id
            subject = named[0].name
            seen_named = True
        elif not seen_named and topic_entity is not None and not is_abstract_close(sentence):
            entity_id = topic_entity.id
            subject = topic_entity.name
        else:
            entity_id = ""
            subject = ""
        shots.append(Shot(
            id=f"s{index:02d}",
            narration=sentence,
            entity_id=entity_id,
            subject=subject,
            canonical_visual="",
            shot_type="",
            camera="",
            setting="",
            era="",
            mood="",
            lighting="",
            negatives=[],
        ))
    normalized = normalize_shots(shots, entities, topic=topic)
    narration = " ".join(shot.narration for shot in normalized)
    plan = ShotList(
        channel=channel,
        topic=" ".join((topic or "").split()),
        narration=narration,
        shots=normalized,
        source={"kind": "topic", "ref": "", "title": ""},
    )
    from production.formats.economic_reel.shot_planner.fit import review_plan

    return review_plan(plan)
