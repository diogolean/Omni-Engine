# -*- coding: utf-8 -*-
"""Concrete documentary prompts for pin photographs.

The channel atmosphere string is not used. A pin scene says what the
subject literally looks like. ``entities.json`` ``pin_scene`` wins. Otherwise
the cheap writer model fills a strict one-sentence template.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Callable

log = logging.getLogger(__name__)

_DEFAULT_STYLE = (
    "Documentary photograph, natural daylight, realistic color, "
    "ordinary photographic detail."
)

PIN_NEGATIVE_GUIDANCE = (
    "No halo, no light beams, no light shafts, no rim light, no glyphs, "
    "no ethereal glow, no volumetric haze, no text, no letters, no logo, no watermark."
)

PIN_NEGATIVE_PROMPT = (
    "halo, light beam, light shaft, god rays, rim light, volumetric lighting, "
    "glyph, ethereal glow, lens flare, text, watermark, logo"
)

_COMPOSITIONS = (
    "Wide view, the subject left of center with the real setting around it.",
    "Three-quarter view from slightly above, natural daylight.",
    "Medium view, the subject off-center, surroundings still visible.",
    "Closer view of the material and surface, the setting still in frame.",
    "Low angle looking along the subject, daylight, no posed portrait.",
)

_BANNED = re.compile(
    r"\b(?:halos?|light[\s-]?shafts?|light[\s-]?beams?|god[\s-]?rays?|"
    r"rim[\s-]?lights?(?:ing)?|volumetric|glyphs?|ethereal|chiaroscuro|"
    r"torchlight|atmospheric\s+haze|alien[\s-]?script)\b",
    re.IGNORECASE,
)

_SCENE_SYSTEM = (
    "You describe one real photograph for an image model. "
    'Reply with JSON only: {"scene": "<one sentence>"}. '
    "The sentence says what the subject literally looks like, what it is made of, "
    "and where it sits. Documentary photograph, natural daylight. "
    "Do not mention halos, light beams, light shafts, rim light, glyphs, "
    "volumetric light, ethereal glow, cinematic color grade, or words in the image."
)

_SCENE_USER = (
    "Title: {title}\n"
    "Article:\n{article}\n\n"
    "Write the scene sentence for this subject only."
)


def pin_image_style(channel_id: str) -> str:
    """Neutral documentary style from ``PIN_IMAGE_STYLE``, never the atmosphere string."""
    from shared.channel_flags import value  # noqa: PLC0415

    raw = str(value(channel_id, "PIN_IMAGE_STYLE", "") or "").strip()
    cleaned = strip_atmosphere(raw)
    return cleaned or _DEFAULT_STYLE


def strip_atmosphere(text: str) -> str:
    """Drop clauses that ask for halos, shafts, rim light, or glyphs."""
    kept: list[str] = []
    for clause in re.split(r"\s*[,;]\s*", text or ""):
        piece = clause.strip()
        if piece and not _BANNED.search(piece):
            kept.append(piece)
    return ", ".join(kept).strip(" ,.")


def composition_line(key: str) -> str:
    digest = hashlib.sha256((key or "pin").encode("utf-8")).digest()[0]
    return _COMPOSITIONS[digest % len(_COMPOSITIONS)]


def _blob(*parts: str) -> str:
    return " ".join(
        part.replace("-", " ").replace("_", " ")
        for part in parts
        if part
    )


def entity_pin_scene(channel_id: str, topic: str, title: str, article: str) -> str:
    """``pin_scene`` from the first entity named in the title or article."""
    if not channel_id:
        return ""
    from production.formats.economic_reel.shot_planner.entities import (  # noqa: PLC0415
        entities_in_text,
        load_entities,
    )

    hits = entities_in_text(_blob(topic, title, article), load_entities(channel_id))
    if not hits:
        return ""
    return (hits[0].pin_scene or "").strip()


def matched_pin_entity(channel: str, entry: dict):
    """The entity named by the pin, when the channel catalogue has one."""
    if not channel:
        return None
    from production.formats.economic_reel.shot_planner.entities import (  # noqa: PLC0415
        entities_in_text,
        load_entities,
    )

    entities = load_entities(channel)
    explicit = str(entry.get("entity") or entry.get("named_entity") or "").strip()
    primary = _blob(
        explicit,
        str(entry.get("topic") or ""),
        str(entry.get("pinterest_title") or ""),
        " ".join(
            str(entry.get(key) or "")
            for key in ("subject_slug", "slug", "article_slug")
        ),
    )
    hits = entities_in_text(primary, entities)
    if not hits:
        hits = entities_in_text(str(entry.get("original_caption") or ""), entities)
    return hits[0] if hits else None


def search_subject(channel: str, entry: dict) -> str:
    """Search words: an explicit entity, else the catalogue subject."""
    explicit = str(entry.get("entity") or entry.get("named_entity") or "").strip()
    if explicit:
        return explicit
    entity = matched_pin_entity(channel, entry)
    if entity is None:
        return ""
    return (entity.search_query or entity.name).strip()


def search_constraints(channel: str, entry: dict) -> dict:
    """Subject plus the terms that keep a free-photo hit on the right place."""
    entity = matched_pin_entity(channel, entry)
    explicit = str(entry.get("entity") or entry.get("named_entity") or "").strip()
    if entity is None:
        return {
            "subject": explicit,
            "required_terms": (),
            "required_any": (),
            "reject_terms": (),
            "allow_illustration": False,
            "allow_biological": False,
        }
    subject = explicit or (entity.search_query or entity.name).strip()
    return {
        "subject": subject,
        "required_terms": entity.required_terms,
        "required_any": entity.required_any,
        "reject_terms": entity.reject_terms,
        "allow_illustration": entity.allow_illustration,
        "allow_biological": entity.allow_biological,
    }


def _parse_scene(raw: str) -> str:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict):
        scene = str(payload.get("scene") or "").strip()
        if scene:
            return scene
    return text


def _fallback_scene(title: str, topic: str) -> str:
    subject = (title or topic or "the subject").strip().rstrip(".")
    return f"A real documentary photograph of {subject} in its actual setting"


def _default_complete(prompt: str, *, system: str) -> str:
    from production.providers.llm.text_model import complete_writer_primary  # noqa: PLC0415

    result = complete_writer_primary(
        prompt,
        system=system,
        json_mode=True,
        max_tokens=220,
        temperature=0.2,
    )
    return str(getattr(result, "text", "") or "")


def scene_sentence(
    *,
    channel_id: str,
    topic: str,
    title: str = "",
    article: str = "",
    complete: Callable[..., str] | None = None,
    allow_model: bool = True,
) -> str:
    """One concrete sentence. The entity override skips the text model."""
    override = strip_atmosphere(entity_pin_scene(channel_id, topic, title, article))
    if override:
        return override
    if allow_model:
        user = _SCENE_USER.format(
            title=(title or topic or "the subject").strip(),
            article=(article or title or topic or "").strip()[:1200],
        )
        try:
            raw = (complete or _default_complete)(user, system=_SCENE_SYSTEM)
            cleaned = strip_atmosphere(_parse_scene(raw))
            if cleaned:
                return cleaned
        except Exception as exc:  # noqa: BLE001
            log.info("Pin scene model skipped (%s).", type(exc).__name__)
    return _fallback_scene(title, topic)


def flux_prompt(
    topic: str,
    channel_id: str,
    *,
    title: str = "",
    article: str = "",
    complete: Callable[..., str] | None = None,
    allow_model: bool = True,
) -> str:
    """Documentary pin prompt. No atmosphere, halo, shaft, rim-light, or glyph language."""
    scene = scene_sentence(
        channel_id=channel_id,
        topic=topic,
        title=title,
        article=article,
        complete=complete,
        allow_model=allow_model,
    ).rstrip(".")
    style = pin_image_style(channel_id).rstrip(".")
    composition = composition_line(title or topic or channel_id).rstrip(".")
    return f"{style}. {scene}. {composition}. {PIN_NEGATIVE_GUIDANCE}"
