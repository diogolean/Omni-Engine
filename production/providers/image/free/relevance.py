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

_BIO_WORD = re.compile(
    r"\b(?:species|frogs?|animals?|birds?|insects?|plants?|genus|taxa|taxon|"
    r"taxonomy|holotypes?|specimens?)\b",
    re.IGNORECASE,
)

# Genus + species, not "Stone ruins" or "The money pit". The epithet has to
# look Latin (granulatus) or the line has to cite an author and year.
_BINOMIAL = re.compile(r"\b[A-Z][a-z]{2,}\s+([a-z]{4,})\b")
_LATIN_EPITHET = re.compile(
    r"(?:ensis|ianus|iana|atus|atum|oides|orum|arum)$",
    re.IGNORECASE,
)
_TAXON_CITE = re.compile(r"\([A-Z][A-Za-z.-]+,\s*\d{3,4}\)")
_BINOMIAL_STOP = frozenset(
    {
        "ruins", "ruin", "stone", "stones", "city", "cities", "island", "islands",
        "jungle", "temple", "history", "photo", "image", "north", "south", "east",
        "west", "building", "museum", "library", "page", "source", "home", "from",
        "with", "near", "national", "reserve", "natural", "park", "peru", "scotia",
        "carolina", "ancient", "lost", "giant", "giants", "inca", "gold", "site",
        "place", "view", "river", "coast", "forest", "valley", "mountain", "tomb",
        "bones", "manuscript", "book", "tablet", "skull", "moai", "statue", "lines",
        "hill", "hills", "water", "beach", "town", "village", "quarry", "garden",
        "trilithon", "money", "focus", "campus", "status", "genius", "bonus",
    }
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


def is_taxonomic(candidate: Candidate) -> bool:
    """A species page or a Latin binomial, not a place photograph."""
    text = candidate_text(candidate)
    if _BIO_WORD.search(text):
        return True
    if _TAXON_CITE.search(text):
        return True
    for match in _BINOMIAL.finditer(text):
        epithet = match.group(1)
        if epithet.lower() in _BINOMIAL_STOP:
            continue
        if _LATIN_EPITHET.search(epithet):
            return True
    return False


def relevance_verdict(
    candidate: Candidate,
    *,
    subject: str,
    required_terms: list[str] | tuple[str, ...] = (),
    reject_terms: list[str] | tuple[str, ...] = (),
    required_any: list[str] | tuple[str, ...] = (),
    allow_illustration: bool = False,
    allow_biological: bool = False,
) -> str:
    """``accept``, ``reject``, or ``ambiguous``."""
    blob = candidate_text(candidate).lower()
    for term in reject_terms:
        if term and term.lower() in blob:
            return "reject"
    if is_taxonomic(candidate) and not allow_biological:
        return "reject"
    if is_illustration(candidate) and not allow_illustration:
        return "reject"
    any_needed = [term for term in required_any if term]
    if any_needed and not any(term.lower() in blob for term in any_needed):
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
    required_any: list[str] | tuple[str, ...] = (),
    allow_illustration: bool = False,
    allow_biological: bool = False,
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
            required_any=required_any,
            allow_illustration=allow_illustration,
            allow_biological=allow_biological,
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
