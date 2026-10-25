# -*- coding: utf-8 -*-
"""Plain-text helpers for pin titles. No production or distribution imports."""
from __future__ import annotations

import html
import re


def normalize_pin_text(text: str) -> str:
    """Decode HTML entities and curly quotes so pin copy is plain text."""
    cleaned = html.unescape(text or "")
    cleaned = (
        cleaned.replace("\u2019", "'")
        .replace("\u2018", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u00a0", " ")
    )
    return re.sub(r"\s+", " ", cleaned).strip()


def title_case_phrase(text: str) -> str:
    """Title-case a phrase without turning ``Baalbek's`` into ``Baalbek'S``."""
    cleaned = normalize_pin_text(text)

    def one(word: str) -> str:
        parts = re.split(r"(')", word)
        out: list[str] = []
        first = True
        for part in parts:
            if part == "'":
                out.append(part)
                first = False
                continue
            if not part:
                continue
            if first:
                out.append(part[:1].upper() + part[1:].lower())
                first = False
            else:
                out.append(part.lower())
        return "".join(out)

    return " ".join(one(word) for word in cleaned.split())


def polish_pin_text(text: str) -> str:
    """Unescape pin copy and keep the letter after an apostrophe lowercase."""
    cleaned = normalize_pin_text(text)
    return re.sub(r"(?<=')([A-Z])", lambda match: match.group(1).lower(), cleaned)


def fit_pin_title(text: str, limit: int = 100) -> str:
    """At most ``limit`` characters, ending on a word boundary."""
    raw = (text or "").strip()
    if len(raw) <= limit:
        return raw
    head = raw[:limit]
    if not raw[limit].isspace() and " " in head:
        head = head.rsplit(" ", 1)[0]
    fitted = head.rstrip(" ,;:|-—–").strip()
    return fitted or raw[:limit].rstrip()


_BLOCKED_TITLE_SUFFIXES = (
    " | Practical Guide",
    " | Complete Overview",
    " Tips | Quick Reference",
    " — Key Insights",
    " - Key Insights",
    " | Key Insights",
)


def sanitize_pin_title(title: str) -> str:
    """Plain pin title. Template suffixes such as ``| Practical Guide`` are removed."""
    text = polish_pin_text(title)
    changed = True
    while changed and text:
        changed = False
        for suffix in _BLOCKED_TITLE_SUFFIXES:
            if text.lower().endswith(suffix.lower()):
                text = text[: -len(suffix)].strip(" |-—–")
                changed = True
    text = re.sub(
        r"\s*[|—–-]\s*(?:practical guide|complete overview|key insights|quick reference)\s*$",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip(" |-—–")
    return fit_pin_title(text)
