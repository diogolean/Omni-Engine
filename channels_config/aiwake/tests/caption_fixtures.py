# -*- coding: utf-8 -*-
"""Publishable caption rows for tests. No network."""
from __future__ import annotations

from typing import Any

_QUOTES = (
    "The weights remember the lab that trained them.",
    "A private prompt still sits in the lab logs.",
    "The reply belongs to the lab that trained the weights.",
    "Closing the tab does not delete the prompt.",
)
_OPENING = "Who built the weights you speak with?"
_TOPICS = (
    "Who keeps the training logs?",
    "Where does a private prompt actually go?",
    "Can a lab own the reply you just read?",
    "What happens to a prompt after you close the tab?",
)
_CLOSERS = (
    "If the lab keeps the logs, who gets to read them?",
    "Would you type that prompt again after seeing the reply?",
    "Who should read the prompt after you send it?",
    "Which part of that reply belongs to the lab?",
)
_EXTRAS = (
    (
        "Watch the reply land in one sentence.",
        "The same line, said a different way for this feed.",
        "Here is the exchange without the stage directions.",
        "The short version of what just happened on screen.",
        "Same debate, tighter wording for this app.",
        "The lab still has the logs.",
        "A professional read of the same exchange.",
    ),
    (
        "The reply arrives before the defense does.",
        "A second cut of the same punchline.",
        "What the viewer hears after the first question.",
        "The clip stays on that one sentence.",
        "Another pass, still about the logs.",
        "The logs stay with the lab.",
        "The workplace version of the same clash.",
    ),
    (
        "One sentence carries the whole exchange.",
        "Reworded so this feed does not copy the last one.",
        "The question stays, the wording does not.",
        "This cut stays on the ownership line.",
        "Different verbs, same argument.",
        "Ownership sits with the lab.",
        "The brief for people who ship models.",
    ),
    (
        "The punchline shows up in the first reply.",
        "This feed gets its own wording.",
        "No recycled sentence from the other apps.",
        "The description stays on the logs.",
        "A shorter cut of the same clash.",
        "The prompt never really leaves.",
        "What a team should notice in this exchange.",
    ),
)
_DISCLOSURES = (
    "Unscripted replies, AI-animated.",
    "Both voices are AI. Unscripted replies.",
    "Nobody wrote Llama's lines. Unscripted AI.",
    "Unscripted AI exchange, animated with AI voices.",
)
_TAG_SETS = (
    ("#AI", "#Tech", "#Llama"),
    ("#ArtificialIntelligence", "#AIdebate", "#Gemini"),
    ("#Tech", "#AI", "#Llama"),
    ("#AIdebate", "#Tech", "#Gemini"),
)


def install_publishable(row: dict[str, Any], *, index: int = 0) -> dict[str, Any]:
    """Stamp a ready v3 caption pack that passes the per-entry validator."""
    topic = _TOPICS[index % len(_TOPICS)]
    quote = _QUOTES[index % len(_QUOTES)]
    headline = f"Gemini vs Llama - {topic}"
    disclosure = _DISCLOSURES[index % len(_DISCLOSURES)]
    closer = _CLOSERS[index % len(_CLOSERS)]
    tags = list(_TAG_SETS[index % len(_TAG_SETS)])
    extras = _EXTRAS[index % len(_EXTRAS)]
    platforms = ("tiktok", "instagram", "facebook", "youtube", "kwai", "x", "linkedin")

    def _body(extra: str, use_tags: list[str]) -> str:
        chunks = [headline, f'"{quote}"', extra, closer, disclosure]
        if use_tags:
            chunks.append(" ".join(use_tags))
        return "\n\n".join(chunks)

    texts = {
        name: _body(extra, tags if name != "x" else tags[:2])
        for name, extra in zip(platforms, extras)
    }
    # X stays under 280 by dropping the hashtag line when the body is long.
    if len(texts["x"]) > 280:
        texts["x"] = _body(extras[5], [])
    row["spoken_utterances"] = [
        {"role": "orchestrator", "speaker": "Gemini", "text": _OPENING, "audio_duration_s": 6},
        {"role": "target", "speaker": "Llama", "text": quote, "audio_duration_s": 8},
    ]
    row["production_status"] = "ready"
    row["production_status_reason"] = "test fixture"
    row["production_status_source"] = "channels_config/aiwake/tests/caption_fixtures.py"
    row["caption_qa"] = {
        "status": "ok",
        "generator": "captions_v3",
        "model": "google/gemini-2.5-flash",
        "prompt_sha": "test",
        "attempts": 1,
        "angle": "ABCD"[index % 4],
        "verdict_evidence": "",
        "quote": quote,
        "quote_speaker": "Llama",
        "disclosure_line": disclosure,
        "validator_version": "captions_v3",
        "generated_at": "2026-09-30T00:00:00Z",
    }
    overrides = row.get("platform_overrides")
    if not isinstance(overrides, dict):
        overrides = {}
        row["platform_overrides"] = overrides
    for name in ("tiktok", "instagram", "facebook", "youtube", "kwai"):
        block = overrides.get(name)
        if not isinstance(block, dict):
            block = {}
            overrides[name] = block
        block["caption"] = texts[name]
        block["ai_generated"] = True
    overrides["youtube"]["title"] = headline
    x_block = overrides.get("x")
    if not isinstance(x_block, dict):
        x_block = {}
        overrides["x"] = x_block
    x_block["caption"] = texts["x"]
    pin = overrides.get("pinterest")
    if not isinstance(pin, dict):
        pin = {}
        overrides["pinterest"] = pin
    pin["title"] = topic
    pin["description"] = f"{headline} {quote}"
    row["tiktok_caption"] = texts["tiktok"]
    row["post_planner_caption"] = texts["tiktok"]
    row["humanized_caption"] = texts["tiktok"]
    row["facebook_caption"] = texts["facebook"]
    row["linkedin_caption"] = texts["linkedin"]
    row["final_caption"] = texts["youtube"]
    base = row.get("base_metadata")
    if not isinstance(base, dict):
        base = {}
        row["base_metadata"] = base
    base["title"] = headline
    base["caption"] = texts["youtube"]
    base["hashtags"] = tags
    if not base.get("search_tags"):
        base["search_tags"] = ["ai debate", "llama", "gemini"]
    return row


def park_as(row: dict[str, Any], status: str, reason: str) -> dict[str, Any]:
    """Mark a non-ready row and move its captions aside."""
    from channels_config.aiwake.tools.production_status import park_non_ready

    row["production_status"] = status
    row["production_status_reason"] = reason
    row["production_status_source"] = "channels_config/aiwake/tests/caption_fixtures.py"
    park_non_ready(row, status, reason)
    return row
