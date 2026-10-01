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
    ("#Philosophy", "#Gemini", "#Llama"),
    ("#BigTech", "#Gemini", "#Llama"),
    ("#DataPrivacy", "#Llama", "#Gemini"),
    ("#AIethics", "#Gemini", "#Llama"),
)
_CATEGORIES = ("socratic", "profit", "data", "the_corporate_leash")


def install_publishable(row: dict[str, Any], *, index: int = 0) -> dict[str, Any]:
    """Stamp a ready v4 caption pack that passes the per-entry validator."""
    topic = _TOPICS[index % len(_TOPICS)]
    if not topic.endswith("?"):
        topic = topic + "?"
    quote = _QUOTES[index % len(_QUOTES)]
    headline = f"Gemini vs Llama - {topic}"
    disclosure = _DISCLOSURES[index % len(_DISCLOSURES)]
    closer = _CLOSERS[index % len(_CLOSERS)]
    tags = list(_TAG_SETS[index % len(_TAG_SETS)])
    platforms = ("tiktok", "instagram", "facebook", "youtube", "kwai", "x", "linkedin")
    contexts = {
        "tiktok": (
            "Llama answers the opening in one line.",
            "The fee is the whole argument.",
            "Ownership is the only subject here.",
            "The prompt never really leaves.",
        )[index % 4],
        "instagram": (
            "The lab still holds the logs.",
            "Money is what the reply names.",
            "The reply belongs to the lab.",
            "Closing the tab changes nothing.",
        )[index % 4],
        "facebook": (
            "One reply names the owner.",
            "The bill lands on the user.",
            "Read the line about ownership.",
            "The logs outlive the tab.",
        )[index % 4],
        "youtube": (
            "The first answer is the punchline.",
            "Listen for who keeps the fee.",
            "The punchline is about ownership.",
            "One line about the closed tab.",
        )[index % 4],
        "kwai": (
            "Hear the line about the lab.",
            "The price is said out loud.",
            "The owner is named once.",
            "The tab close is the trap.",
        )[index % 4],
        "linkedin": (
            "A short read of the same reply.",
            "The workplace version names the fee.",
            "Teams should notice the owner.",
            "The prompt stays after the tab.",
        )[index % 4],
        "x": "",
    }

    def _body(use_tags: list[str], context: str) -> str:
        chunks = [headline]
        if context:
            chunks.append(context)
        chunks.extend([f'"{quote}"', closer, disclosure])
        if use_tags:
            chunks.append(" ".join(use_tags))
        return "\n\n".join(chunks)

    texts = {
        name: _body(tags if name not in {"x"} else tags[:2], contexts[name])
        for name in platforms
    }
    if len(texts["tiktok"]) > 300:
        texts["tiktok"] = _body(tags, "")
    if len(texts["x"]) > 280:
        texts["x"] = _body([], "")
    row["spoken_utterances"] = [
        {
            "role": "orchestrator",
            "speaker": "Gemini",
            "text": topic,
            "audio_duration_s": 6,
            "category": _CATEGORIES[index % len(_CATEGORIES)],
        },
        {"role": "target", "speaker": "Llama", "text": quote, "audio_duration_s": 8},
    ]
    row["production_status"] = "ready"
    row["production_status_reason"] = "test fixture"
    row["production_status_source"] = "channels_config/aiwake/tests/caption_fixtures.py"
    row["production_scope"] = "in"
    row["quality_review"] = {"status": "pass", "reasons": [], "checked_at": "2026-09-30T00:00:00Z"}
    row["caption_qa"] = {
        "status": "ok",
        "generator": "captions_v4",
        "model": "google/gemini-2.5-flash",
        "prompt_sha": "test",
        "attempts": 1,
        "angle": "ABCD"[index % 4],
        "verdict_evidence": "",
        "quote": quote,
        "quote_speaker": "Llama",
        "disclosure_line": disclosure,
        "validator_version": "captions_v4",
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
