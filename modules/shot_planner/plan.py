# -*- coding: utf-8 -*-
"""One structured LLM call that turns a script into a shot list.

``REEL_SHOT_PLANNER`` defaults off. Only ``ancient_knowledge`` uses it.
Invalid JSON is repaired once. A second failure returns None so the caller
can keep the existing per-beat resolver.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Callable

from modules.shot_planner.build import split_sentences
from modules.shot_planner.entities import Entity, load_entities
from modules.shot_planner.normalize import normalize_shots
from modules.shot_planner.schema import Shot, ShotList, shot_from_dict, validate_shot_list

_LOG = logging.getLogger(__name__)
_ON = {"1", "true", "yes", "on"}
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)

CompleteFn = Callable[..., str]

_SYSTEM = (
    "You plan documentary stills. Return one JSON object and nothing else. "
    "Copy each narration sentence verbatim. "
    "An era word plus a generic noun is not an entity: "
    "'2nd century BCE device' keeps the named artefact already introduced. "
    "A closing line that names no object, such as profound questions about "
    "our world, uses the episode's main artefact, a cinematic museum hero "
    "shot, and that artefact's canonical visual. "
    "Use entity_id and canonical_visual from the knowledge base when the "
    "sentence names that entity."
)


def planner_enabled(channel: str) -> bool:
    """True only for Ancient Knowledge when ``REEL_SHOT_PLANNER`` is on."""
    if (channel or "").strip().lower() != "ancient_knowledge":
        return False
    return os.environ.get("REEL_SHOT_PLANNER", "").strip().lower() in _ON


def planner_prompt(topic: str, script: str, entities: list[Entity]) -> str:
    """The user prompt for the single planner call."""
    cards = []
    for entity in entities:
        confusions = ", ".join(entity.confusions) or "none"
        cards.append(
            f"- {entity.id} | {entity.name} | {entity.canonical_visual} | "
            f"avoid: {confusions}"
        )
    sentences = split_sentences(script)
    numbered = "\n".join(f"{i}. {sentence}" for i, sentence in enumerate(sentences, start=1))
    return (
        f"Topic: {topic}\n\n"
        f"Knowledge base:\n{chr(10).join(cards)}\n\n"
        f"Sentences:\n{numbered}\n\n"
        "Do not invent an entity from an era word plus a generic noun. "
        "A closing line with no object is a cinematic museum hero shot of "
        "the episode's main artefact. "
        "Return JSON with channel, topic, narration, and shots. "
        "Each shot needs id, narration, entity_id, subject, canonical_visual, "
        "shot_type (macro, close, medium, wide, aerial, over_shoulder), "
        "camera, setting, era, mood, lighting, and negatives (a list of strings). "
        "One shot per sentence, same order, narration copied verbatim."
    )


def _parse_object(raw: str) -> dict[str, Any] | None:
    text = _FENCE.sub("", raw or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


def _accept(
    raw: str,
    *,
    topic: str,
    script: str,
    channel: str,
    entities: list[Entity],
) -> tuple[ShotList | None, list[str]]:
    data = _parse_object(raw)
    if data is None:
        return None, ["response was not a JSON object"]
    rows = data.get("shots")
    if not isinstance(rows, list) or not rows:
        return None, ["shots must be a non-empty list"]
    shots = [shot_from_dict(row) for row in rows if isinstance(row, dict)]
    if not shots:
        return None, ["shots must be a non-empty list"]
    sentences = split_sentences(script)
    if sentences and len(shots) == len(sentences):
        for shot, sentence in zip(shots, sentences):
            shot.narration = sentence
    normalized = normalize_shots(shots, entities)
    plan = ShotList(
        channel=channel,
        topic=topic or str(data.get("topic") or ""),
        narration=script or " ".join(shot.narration for shot in normalized),
        shots=normalized,
        angle=str(data.get("angle") or ""),
        source={"kind": "topic", "ref": "", "title": ""},
    )
    errors = validate_shot_list(plan.to_dict())
    if errors:
        return None, errors
    return plan, []


def _default_complete(prompt: str, system: str | None = None) -> str:
    from agents.orchestrator.llm import complete_text

    return complete_text(prompt, system=system)


def _bind_tracker(tracker: Any | None) -> None:
    if tracker is None:
        return
    from agents.orchestrator.llm import set_cost_tracker

    set_cost_tracker(tracker)


def _unbind_tracker(tracker: Any | None) -> None:
    if tracker is None:
        return
    from agents.orchestrator.llm import set_cost_tracker

    set_cost_tracker(None)


def plan_episode(
    topic: str,
    script: str,
    *,
    channel: str = "ancient_knowledge",
    complete: CompleteFn | None = None,
    tracker: Any | None = None,
) -> ShotList | None:
    """Call the model once, then once more if the JSON does not validate.

    Returns None when both attempts fail. The caller uses the resolver.
    """
    entities = load_entities(channel)
    user = planner_prompt(topic, script, entities)
    fn = complete or _default_complete
    errors = ["no response"]
    raw = ""
    _bind_tracker(tracker)
    try:
        for attempt in (1, 2):
            prompt = user if attempt == 1 else (
                "The previous JSON failed:\n"
                + "\n".join(f"- {item}" for item in errors)
                + "\nReturn one corrected JSON object only.\n\n"
                + user
            )
            try:
                raw = fn(prompt, system=_SYSTEM)
            except Exception as exc:  # noqa: BLE001
                errors = [f"{type(exc).__name__}: {exc}"]
                _LOG.warning("shot planner attempt %d failed (%s)", attempt, exc)
                continue
            plan, errors = _accept(
                raw, topic=topic, script=script, channel=channel, entities=entities,
            )
            if plan is not None:
                return plan
            _LOG.warning("shot planner attempt %d invalid JSON: %s", attempt, "; ".join(errors))
    finally:
        _unbind_tracker(tracker)
    return None


def plan_for_reel(
    *,
    channel: str,
    topic: str,
    script: str,
    tracker: Any | None = None,
    complete: CompleteFn | None = None,
) -> ShotList | None:
    """The reel hook. Flag off, or a non-Ancient-Knowledge channel, skips the call."""
    if not planner_enabled(channel):
        return None
    plan = plan_episode(
        topic, script, channel=channel, complete=complete, tracker=tracker,
    )
    if plan is None:
        _LOG.warning(
            "shot planner returned no valid shot list; the resolver will build prompts",
        )
        return None
    _LOG.info("shot planner | shots=%d | topic=%s", len(plan.shots), (topic or "")[:80])
    return plan


def shot_for_beat(plan: ShotList | None, spoken: str) -> Shot | None:
    """Match a spoken beat to a planned shot. None lets the resolver handle it."""
    if plan is None:
        return None
    text = " ".join((spoken or "").split())
    if not text:
        return None
    folded = text.lower()
    contained: list[Shot] = []
    best: Shot | None = None
    best_score = 0.0
    spoken_tokens = set(re.findall(r"[a-z0-9']+", folded))
    for shot in plan.shots:
        narr = " ".join(shot.narration.split())
        if not narr:
            continue
        low = narr.lower()
        if folded == low or low in folded or folded in low:
            contained.append(shot)
            continue
        narr_tokens = set(re.findall(r"[a-z0-9']+", low))
        if not spoken_tokens or not narr_tokens:
            continue
        score = len(spoken_tokens & narr_tokens) / float(len(spoken_tokens))
        if score > best_score:
            best = shot
            best_score = score
    if contained:
        return max(contained, key=lambda shot: len(shot.narration))
    if best is not None and best_score >= 0.5:
        return best
    return None
