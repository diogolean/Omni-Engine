# -*- coding: utf-8 -*-
"""License allowlist for commercial reuse of sourced photographs.

Default accepts Public Domain, CC0, CC BY, CC BY-SA, and the Pexels and
Unsplash licenses. NC and ND terms are rejected because the channels
publish commercially and the card / reel treatment is a derivative.
"""

from __future__ import annotations

import os
import re

CANONICAL_LICENSES: tuple[str, ...] = (
    "public_domain",
    "cc0",
    "cc_by",
    "cc_by_sa",
    "pexels",
    "unsplash",
)

DISPLAY_NAMES: dict[str, str] = {
    "public_domain": "Public Domain",
    "cc0": "CC0",
    "cc_by": "CC BY",
    "cc_by_sa": "CC BY-SA",
    "pexels": "Pexels License",
    "unsplash": "Unsplash License",
}

_NC = re.compile(r"\bnc\b|non-?commercial|noncommercial", re.IGNORECASE)
_ND = re.compile(r"\bnd\b|no-?deriv", re.IGNORECASE)
_CC0 = re.compile(r"\bcc0\b|cc-?zero|public-domain-mark|\bpdm\b", re.IGNORECASE)
_BY_SA = re.compile(
    r"\bby-sa\b|by sa|attribution-sharealike|cc-by-sa",
    re.IGNORECASE,
)
_BY = re.compile(r"\bby\b|attribution", re.IGNORECASE)


def display_license(canonical: str) -> str:
    return DISPLAY_NAMES.get(canonical, canonical)


def default_allowlist() -> frozenset[str]:
    raw = (os.getenv("FREE_IMAGE_ALLOW_LICENSES") or "").strip()
    if not raw:
        return frozenset(CANONICAL_LICENSES)
    parsed = {
        item.strip().lower().replace("-", "_").replace(" ", "_")
        for item in raw.split(",")
        if item.strip()
    }
    allowed = parsed & set(CANONICAL_LICENSES)
    return frozenset(allowed or CANONICAL_LICENSES)


def classify_license(raw: str) -> str | None:
    """Map a provider license string to a canonical id, or None when unusable."""
    text = (raw or "").strip().lower().replace("_", "-")
    if not text:
        return None
    if "unsplash" in text:
        return "unsplash"
    if "pexels" in text:
        return "pexels"
    if _NC.search(text) or _ND.search(text):
        return None
    if "all rights reserved" in text or "copyright restricted" in text:
        return None
    if "no known restrictions" in text or "no restrictions" in text:
        return "public_domain"
    if _CC0.search(text):
        return "cc0"
    if "public domain" in text or text in {"pd", "pdm"}:
        return "public_domain"
    if _BY_SA.search(text):
        return "cc_by_sa"
    if _BY.search(text):
        return "cc_by"
    return None


def license_allowed(raw: str, allowlist: frozenset[str] | set[str] | None = None) -> str | None:
    canonical = classify_license(raw)
    if canonical is None:
        return None
    allowed = frozenset(allowlist) if allowlist is not None else default_allowlist()
    if canonical not in allowed:
        return None
    return canonical
