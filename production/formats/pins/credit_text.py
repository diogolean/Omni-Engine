# -*- coding: utf-8 -*-
"""Credit line text for the pin card.

A Wikimedia author name sometimes arrives as UTF-8 bytes read as Latin-1
(``Ø¹Ø¨Ø¯…``). That string is restored before it is drawn. Arabic is then
shaped and reordered so Pillow, which draws left to right and does not
shape, shows the name instead of isolated or reversed letters.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# Arabic blocks, including the presentation forms a shaper emits.
_ARABIC_RANGES = (
    (0x0600, 0x06FF),
    (0x0750, 0x077F),
    (0x08A0, 0x08FF),
    (0xFB50, 0xFDFF),
    (0xFE70, 0xFEFF),
)
_MOJIBAKE_MARKS = frozenset("ØÙÚÛ")


def _is_arabic(ch: str) -> bool:
    code = ord(ch)
    return any(start <= code <= end for start, end in _ARABIC_RANGES)


def has_arabic(text: str) -> bool:
    return any(_is_arabic(ch) for ch in text or "")


def repair_credit_encoding(text: str) -> str:
    """Restore a UTF-8 credit that was decoded as Latin-1.

    A name that already contains Arabic is unchanged. A Latin credit with
    none of the mojibake marks is unchanged.
    """
    raw = (text or "").strip()
    if not raw or has_arabic(raw):
        return raw
    if not any(ch in _MOJIBAKE_MARKS for ch in raw):
        return raw
    try:
        repaired = raw.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return raw
    if repaired != raw and has_arabic(repaired):
        return repaired
    return raw


def _run_kind(ch: str) -> str:
    """``R`` Arabic, ``L`` Latin or European digits, ``N`` neutral."""
    if _is_arabic(ch):
        return "R"
    if ch.isalpha() or ch.isdigit():
        return "L"
    return "N"


def visual_order(text: str) -> str:
    """One-line bidi. Latin stays readable; an Arabic run is reversed."""
    if not text or not has_arabic(text):
        return text
    kinds = [_run_kind(ch) for ch in text]
    base = "L"
    for kind in kinds:
        if kind != "N":
            base = kind
            break
    resolved = list(kinds)
    index = 0
    count = len(resolved)
    while index < count:
        if resolved[index] != "N":
            index += 1
            continue
        start = index
        while index < count and resolved[index] == "N":
            index += 1
        prev = base
        for earlier in range(start - 1, -1, -1):
            if resolved[earlier] != "N":
                prev = resolved[earlier]
                break
        nxt = base
        for later in range(index, count):
            if resolved[later] != "N":
                nxt = resolved[later]
                break
        fill = prev if prev == nxt else base
        for slot in range(start, index):
            resolved[slot] = fill
    if base == "L":
        levels = [0 if kind == "L" else 1 for kind in resolved]
    else:
        levels = [1 if kind == "R" else 2 for kind in resolved]
    chars = list(text)
    highest = max(levels) if levels else 0
    for level in range(highest, 0, -1):
        index = 0
        while index < count:
            if levels[index] < level:
                index += 1
                continue
            start = index
            while index < count and levels[index] >= level:
                index += 1
            chars[start:index] = reversed(chars[start:index])
            levels[start:index] = reversed(levels[start:index])
    return "".join(chars)


def shape_credit_line(text: str) -> str:
    """Logical credit to the left-to-right string the card draws."""
    repaired = repair_credit_encoding(text)
    if not has_arabic(repaired):
        return repaired
    try:
        import arabic_reshaper
    except ImportError:
        log.warning(
            "arabic-reshaper is not installed; the Arabic credit is reordered only."
        )
        return visual_order(repaired)
    return visual_order(arabic_reshaper.reshape(repaired))
