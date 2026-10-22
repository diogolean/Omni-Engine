# -*- coding: utf-8 -*-
"""Master-copy cascade for Aiwake titles and platform captions.

One anchor per episode (core hook + verbatim killer quote). Titles stay under
55 characters. YouTube descriptions carry the model string and a short turn
map. No chapter timestamps.
"""
from __future__ import annotations

import re
from typing import Any

TITLE_MAX_CHARS = 70
MODEL_ATTRIBUTION = "Gemini 3.5 Flash vs Llama 3.3 70B"
_PORTUGUESE_LEAK = re.compile(r"ele falou|falou isso|mandou isso", re.IGNORECASE)
_PORTUGUESE_WORD = re.compile(
    r"\b(?:não|nao|você|voce|está|estão|também|tambem|porque|falou|mandou|"
    r"isso|ele|ela|uma|para|pelo|pela|dos|das|num|numa|você)\b",
    re.IGNORECASE,
)
_PORTUGUESE_CHAR = re.compile(r"[ãõçáàâéêíóôú]")
_DUMP_LINE_RE = re.compile(
    r"\b(?:Gemini|Llama|GPT|Claude|Grok)\b[^:\n]{0,40}\b(?:opens|answers|presses|holds|concedes):",
    re.IGNORECASE,
)
SOCIAL_HASHTAGS = ("#AI", "#Tech", "#ArtificialIntelligence")
LINKEDIN_HASHTAGS = ("#AI", "#MachineLearning", "#LLM")
_CHAPTER_RE = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")


def title_too_long(title: str) -> bool:
    return len(" ".join(str(title or "").split())) > TITLE_MAX_CHARS


def has_chapter_timestamps(text: str) -> bool:
    return bool(_CHAPTER_RE.search(str(text or "")))


def _clip_chars(text: str, limit: int) -> str:
    cleaned = " ".join(str(text or "").split()).strip()
    if len(cleaned) <= limit:
        return cleaned
    trimmed = cleaned[:limit].rsplit(" ", 1)[0].rstrip(" .,;:—-")
    return trimmed or cleaned[:limit].rstrip()


def _short_brand(name: str) -> str:
    lowered = str(name or "").lower()
    if "gemini" in lowered:
        return "Gemini"
    if "llama" in lowered:
        return "Llama"
    token = str(name or "").split()
    return token[0] if token else "AI"


def master_anchor(dialogue: dict[str, Any], *, attacker: str, defender: str) -> dict[str, str]:
    """Core hook plus the verbatim line where the reply seat is cornered."""
    opening = " ".join(str(dialogue.get("opening") or "").split()).strip()
    quote = " ".join(str(dialogue.get("quote") or "").split()).strip().strip('"')
    if not opening:
        return {"core_hook": "", "killer_quote": quote}
    if opening[-1:] not in "?!.":
        opening = f"{opening}?"
    return {"core_hook": opening, "killer_quote": quote}


def format_ctr_title(
    opening: str,
    *,
    attacker: str = "Gemini",
    defender: str = "Llama",
    seed: str = "",
) -> str:
    """Keep the spoken hook. Do not drop words or write an 'X caught Y' title."""
    _ = (attacker, defender, seed)
    hook = " ".join(str(opening or "").split()).strip()
    hook = re.sub(r"\s+[—-]\s+(?:Gemini|Llama|GPT|Claude).*$", "", hook, flags=re.IGNORECASE)
    if hook and hook[-1:] not in "?!.":
        hook = f"{hook}?"
    return hook


def has_transcript_dump(text: str) -> bool:
    return bool(_DUMP_LINE_RE.search(str(text or "")))


def is_portuguese(text: str) -> bool:
    """True when a caption line is Portuguese rather than native English."""
    raw = str(text or "")
    if _PORTUGUESE_LEAK.search(raw) or _PORTUGUESE_CHAR.search(raw):
        return True
    return len(_PORTUGUESE_WORD.findall(raw)) >= 2


def english_line(text: str, fallback: str = "") -> str:
    """Keep a fluent English line. Never substitute a fabricated sentence."""
    _ = fallback
    cleaned = " ".join(str(text or "").split()).strip()
    if not cleaned or is_portuguese(cleaned):
        return ""
    return cleaned


def pick_comment_prompt(hook: str, trigger: str, seed: str) -> str:
    """Return the English trigger when it is usable. No stock closer."""
    _ = (hook, seed)
    return english_line(trigger)


def build_social_caption(
    anchor: dict[str, str],
    *,
    attacker: str = "",
    defender: str = "",
    tags: tuple[str, ...] = SOCIAL_HASHTAGS,
    prompt: str = "",
) -> str:
    """Captions are written by captions_v3. This legacy builder stays empty."""
    _ = (anchor, attacker, defender, tags, prompt)
    return ""


def build_youtube_caption(
    anchor: dict[str, str],
    dialogue: dict[str, Any],
    *,
    attacker: str,
    defender: str,
    tags: tuple[str, ...] = SOCIAL_HASHTAGS,
) -> str:
    """Captions are written by captions_v3. This legacy builder stays empty."""
    _ = (anchor, dialogue, attacker, defender, tags)
    return ""


def build_linkedin_caption(anchor: dict[str, str], *, seed: str = "") -> str:
    """Captions are written by captions_v3. This legacy builder stays empty."""
    _ = (anchor, seed)
    return ""


def apply_master_copy(
    row: dict[str, Any],
    dialogue: dict[str, Any],
    *,
    attacker: str,
    defender: str,
) -> dict[str, str]:
    """Title only. Platform sentences come from the v3 caption generator."""
    anchor = master_anchor(dialogue, attacker=attacker, defender=defender)
    title = format_ctr_title(english_line(anchor["core_hook"]), attacker=attacker, defender=defender)
    return {
        "title": title,
        "core_hook": anchor["core_hook"],
        "killer_quote": anchor["killer_quote"],
        "social": "",
        "youtube": "",
        "linkedin": "",
    }

