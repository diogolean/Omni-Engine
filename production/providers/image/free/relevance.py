# -*- coding: utf-8 -*-
"""Reject free-image hits that are not the subject.

Token overlap runs first. A cheap text model is asked only when the
metadata is ambiguous. Illustrations and book scans lose to photographs
unless the subject is itself a document.
"""

from __future__ import annotations

import json
import logging
import re

from production.providers.image.free.models import Candidate
from production.providers.image.free.query import tokenize

logger = logging.getLogger(__name__)

_GENERIC = frozenset(
    {
        "island", "islands", "ruins", "ruin", "city", "cities", "stone", "stones",
        "ancient", "temple", "building", "photo", "image", "view", "north",
        "south", "west", "east", "book", "history", "old", "great", "lost",
        "gold", "site", "place", "world",
    }
)

_ILLUSTRATION = re.compile(
    r"\b(?:illustrations?|engravings?|drawings?|sketches?|diagrams?|"
    r"lithographs?|woodcuts?|frontispieces?|book\s+plates?|title\s+pages?|"
    r"scanned\s+pages?|book\s+scans?|plates?\s+from|from\s+the\s+book)\b",
    re.IGNORECASE,
)

_JUDGE_SYSTEM = (
    "You check whether image-search metadata is the named subject. "
    "A different place that shares part of the name is not relevant. "
    "A book illustration of an unrelated building is not relevant. "
    'Reply with JSON only: {"relevant": true} or {"relevant": false}.'
)


def candidate_text(candidate: Candidate) -> str:
    return f"{candidate.title}\n{candidate.description}"


def is_illustration(candidate: Candidate) -> bool:
    return bool(_ILLUSTRATION.search(candidate_text(candidate)))


def relevance_verdict(
    candidate: Candidate,
    *,
    subject: str,
    required_terms: list[str] | tuple[str, ...] = (),
    reject_terms: list[str] | tuple[str, ...] = (),
    allow_illustration: bool = False,
) -> str:
    """``accept``, ``reject``, or ``ambiguous``."""
    blob = candidate_text(candidate).lower()
    for term in reject_terms:
        if term and term.lower() in blob:
            return "reject"
    if is_illustration(candidate) and not allow_illustration:
        return "reject"
    title_tokens = set(tokenize(blob))
    distinctive = [token for token in tokenize(subject) if token not in _GENERIC]
    if not distinctive:
        distinctive = tokenize(subject)
    hits = [token for token in distinctive if token in title_tokens]
    missing = [term for term in required_terms if term and term.lower() not in blob]
    if missing:
        partial = False
        for term in missing:
            words = [word for word in tokenize(term) if word not in _GENERIC]
            if any(word in title_tokens for word in words):
                partial = True
        if hits or partial:
            return "ambiguous"
        return "reject"
    if hits:
        return "accept"
    if not distinctive:
        return "accept"
    return "reject"


def cheap_relevance_judge(subject: str, candidate: Candidate) -> bool:
    """DeepSeek Flash. A failure rejects the candidate."""
    try:
        from production.providers.llm.text_model import complete_writer_primary
    except Exception as exc:  # noqa: BLE001
        logger.info("Relevance judge unavailable (%s).", type(exc).__name__)
        return False
    prompt = (
        f"Subject: {subject}\n"
        f"Title: {(candidate.title or '')[:300]}\n"
        f"Description: {(candidate.description or '')[:400]}"
    )
    try:
        result = complete_writer_primary(
            prompt,
            system=_JUDGE_SYSTEM,
            json_mode=True,
            max_tokens=40,
            temperature=0.0,
        )
        payload = json.loads(str(getattr(result, "text", "") or ""))
    except Exception as exc:  # noqa: BLE001
        logger.info("Relevance judge failed (%s).", type(exc).__name__)
        return False
    if not isinstance(payload, dict):
        return False
    return bool(payload.get("relevant"))


def select_relevant(
    candidates: list[Candidate],
    *,
    subject: str,
    required_terms: list[str] | tuple[str, ...] = (),
    reject_terms: list[str] | tuple[str, ...] = (),
    allow_illustration: bool = False,
    judge=None,
) -> list[Candidate]:
    """Photographs that match the subject. The judge sees ambiguous rows only."""
    accepted: list[Candidate] = []
    ambiguous: list[Candidate] = []
    for candidate in candidates:
        verdict = relevance_verdict(
            candidate,
            subject=subject,
            required_terms=required_terms,
            reject_terms=reject_terms,
            allow_illustration=allow_illustration,
        )
        if verdict == "accept":
            accepted.append(candidate)
        elif verdict == "ambiguous":
            ambiguous.append(candidate)
    pool = list(accepted)
    if not pool and ambiguous and judge is not False:
        judge_fn = cheap_relevance_judge if judge is None else judge
        for candidate in ambiguous[:3]:
            try:
                ok = bool(judge_fn(subject, candidate))
            except Exception:  # noqa: BLE001
                ok = False
            if ok:
                pool.append(candidate)
    photos = [candidate for candidate in pool if not is_illustration(candidate)]
    if photos:
        return photos
    if allow_illustration:
        return pool
    return []
