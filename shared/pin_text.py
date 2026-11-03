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


_DANGLING_TITLE = frozenset(
    {
        "a", "an", "the", "of", "in", "on", "to", "for", "and", "or", "with",
        "from", "by", "at", "into", "over",
    }
)
_CLAUSE_BOUNDARY = re.compile(r"[.?!]|—|–|;|:|(?<=\s)-(?=\s)")


def is_dangling_pin_word(word: str) -> bool:
    """A trailing article, preposition, conjunction, or bare punctuation mark."""
    bare = (word or "").lower().strip(".,;:!?\"'—–|-")
    return not bare or bare in _DANGLING_TITLE


def has_clause_boundary(text: str) -> bool:
    """True when *text* has a sentence end, colon, semicolon, or dash."""
    return bool(_CLAUSE_BOUNDARY.search(text or ""))


def clause_prefixes(text: str) -> list[str]:
    """Nonempty prefixes that end on a clause boundary, short to long.

    The boundary marker stays on a sentence (``.?!``) and drops off a
    dash, colon, or semicolon, so the prefix is the clause itself.
    """
    found: list[str] = []
    for match in _CLAUSE_BOUNDARY.finditer(text or ""):
        token = match.group()
        if token in ".?!":
            prefix = text[: match.end()].strip()
        else:
            prefix = text[: match.start()].strip(" \t,;:|-—–")
        if prefix and (not found or prefix != found[-1]):
            found.append(prefix)
    return found


def cuts_mid_clause(short: str, full: str) -> bool:
    """True when *short* is a leading fragment of *full* inside one clause.

    ``The Nazca Lines: Who Made`` against ``... Who Made Them`` is a cut.
    A fragment that stops on the colon or dash is a finished clause.
    """
    sw = (short or "").split()
    fw = (full or "").split()
    if not sw or len(sw) >= len(fw):
        return False

    def key(word: str) -> str:
        return re.sub(r"[^a-z0-9']+", "", word.lower())

    if [key(word) for word in sw] != [key(word) for word in fw[: len(sw)]]:
        return False
    prev = fw[len(sw) - 1]
    nxt = fw[len(sw)]
    if any(ch in prev for ch in ".?!—–;:"):
        return False
    if nxt[:1] in ".?!—–;:":
        return False
    return True


def _strip_dangling_title(raw: str) -> str:
    words = (raw or "").split()
    while len(words) > 1 and is_dangling_pin_word(words[-1]):
        words.pop()
    return " ".join(words)


def _title_key(text: str) -> str:
    return re.sub(r"[^a-z0-9']+", " ", (text or "").lower()).strip()


def _segment_already_said(key: str, earlier: list[str]) -> bool:
    """A later colon clause that repeats an earlier one, including a cut-off copy."""
    words = key.split()
    if not key:
        return False
    for prev in earlier:
        if key == prev:
            return True
        if len(words) < 3:
            continue
        padded = f" {prev} "
        if f" {key} " in padded or padded.endswith(f" {key} "):
            return True
    return False


def dedupe_pin_title(text: str) -> str:
    """Drop a colon clause that repeats an earlier one, then a doubled word run.

    ``Giants: Giants`` becomes ``Giants``. A headline joined to a second
    title that says the same clause again keeps the first copy, even when
    the join was already cut at 100 characters.
    """
    raw = (text or "").strip()
    if not raw:
        return ""
    parts = [part.strip(" ,;:-—–|") for part in re.split(r"\s*[:|—–]\s*", raw)]
    parts = [part for part in parts if part]
    if len(parts) > 1:
        kept: list[str] = []
        seen: list[str] = []
        for part in parts:
            key = _title_key(part)
            if _segment_already_said(key, seen):
                continue
            kept.append(part)
            seen.append(key)
        raw = ": ".join(kept)
    spans = list(re.finditer(r"[A-Za-z0-9']+", raw))
    folded = [span.group(0).lower() for span in spans]
    count = len(folded)
    best_cut = 0
    if count >= 4:
        for start in range(3, count):
            remain = count - start
            if remain < 3 or remain > start:
                continue
            left = folded[:remain]
            right = folded[start : start + remain]
            same = left == right
            clipped = (
                left[:-1] == right[:-1]
                and len(right[-1]) >= 3
                and left[-1].startswith(right[-1])
            )
            if (same or clipped) and start > best_cut:
                best_cut = start
    if best_cut:
        raw = raw[: spans[best_cut - 1].end()].strip(" ,;:|-—–")
    words = raw.split()
    if len(words) >= 2 and len(words) % 2 == 0:
        half = len(words) // 2
        if [_title_key(word) for word in words[:half]] == [
            _title_key(word) for word in words[half:]
        ]:
            raw = " ".join(words[:half]).strip(" ,;:|-—–")
    return raw.strip()


def fit_pin_title(text: str, limit: int = 100) -> str:
    """At most ``limit`` characters, ending on a full clause or a whole word.

    A title that does not fit keeps the longest complete clause inside the
    limit. With no clause boundary, it ends on a word. A trailing
    preposition or conjunction is dropped, so the line never stops on
    ``Who Made`` when ``The Nazca Lines`` is a finished clause that fits.
    """
    raw = (text or "").strip()
    if not raw:
        return ""
    if len(raw) <= limit:
        return _strip_dangling_title(raw)
    best = ""
    for prefix in clause_prefixes(raw):
        if len(prefix) <= limit and len(prefix) > len(best):
            best = prefix
    if best:
        stripped = _strip_dangling_title(best)
        if stripped:
            return stripped
    head = raw[:limit]
    if not raw[limit].isspace() and " " in head:
        head = head.rsplit(" ", 1)[0]
    head = head.rstrip(" ,;:|-—–").strip() or raw[:limit].rstrip()
    return _strip_dangling_title(head) or head


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
    return fit_pin_title(dedupe_pin_title(text))
