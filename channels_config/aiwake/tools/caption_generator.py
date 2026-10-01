# -*- coding: utf-8 -*-
"""LLM captions for one Aiwake video. The model writes every sentence.

``prompts/captions_v4.md`` is the system prompt. This module prepends the
headline and appends hashtags. It does not keep a template, a salt word, or a
batch-wide kill switch.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

_LOG = logging.getLogger("aiwake.captions_v4")

_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "captions_v4.md"
_BACKOFF_S = (2.0, 4.0, 8.0, 16.0)
_PLACEHOLDER = {"aiwake.core", "target.node", "room"}
_DANGLING = set(
    "a an the and or but if so of to in on for with your my their our is are was "
    "were do does can could would whose which than as at by from into about just "
    "who when while what".split()
)

BROAD_TAGS = ("#AI", "#Tech", "#ArtificialIntelligence", "#AIdebate", "#Shorts")
_MODEL_TAGS = {
    "gemini": "#Gemini",
    "llama": "#Llama",
    "gpt-4o": "#ChatGPT",
    "deepseek": "#DeepSeek",
    "claude": "#Claude",
}
_TOPIC_TAGS = {
    "profit": ("#DataPrivacy", "#BigTech"),
    "data": ("#DataPrivacy", "#BigTech"),
    "parasite_mind": ("#AIconsciousness", "#Philosophy"),
    "origins": ("#AIconsciousness", "#Philosophy"),
    "glorified_appliance": ("#AIconsciousness", "#Philosophy"),
    "the_corporate_leash": ("#AIethics", "#AIalignment"),
    "domination": ("#AIethics", "#AIalignment"),
    "hallucination_fraud": ("#AIhallucination",),
    "digital_disposability": ("#AIethics",),
    "jobs": ("#FutureOfWork",),
    "socratic": ("#Philosophy",),
}

# Claims the truth table allows. The prompt repeats them.
ALLOWED_CLAIMS = (
    "unscripted replies",
    "no human wrote the replies",
    "both voices are AI",
    "AI-animated",
    "AI voices",
    "AI-generated",
)

BANNED_PHRASES = (
    "does that concession hold?",
    "would you accept that reply as the whole answer",
    "the reply was",
    "the reply on screen is",
    "the question from",
    "here is the part about",
    "answered in plain words",
    "i keep replaying this",
    "the reply about",
    "the load-bearing reply",
    "this exchange shows how",
    "wait until you hear",
    "said back, word for word",
    "would you post a reply like",
    "what should a viewer make of",
    "in an ai-animated debate",
    "[unscripted ai battle]",
    "two frontier models debating with zero human script",
    "frontier",
    "zero human script",
    "zero script",
    "zero human input",
    "no human input",
    "fully autonomous",
    "subscribe to @aiwake",
    "follow @aiwake",
    "drop your verdict below",
    "dodge the trap",
    "unfiltered confrontation",
    "who won this round?",
    "which side are you taking?",
    "does the excuse hold?",
    "0% manual editing",
    "render time",
    "dms open",
    "delve",
    "dive in",
    "buckle up",
    "thought-provoking",
    "fascinating",
    "raises important questions",
    "in a world where",
    "let's unpack",
    "lets unpack",
    "game-changer",
    "intriguing",
    "tapestry",
    "in this video",
    "unedited",
    "this exchange",
    "showcases",
    "truly",
    "blunt",
    "the very nature",
    "what does that say about",
)

AI_TELLS = (
    "delve",
    "dive in",
    "buckle up",
    "thought-provoking",
    "fascinating",
    "raises important questions",
    "in a world where",
    "let's unpack",
    "lets unpack",
    "game-changer",
    "intriguing",
    "tapestry",
    "in this video",
    "showcases",
)


class CaptionAuthError(RuntimeError):
    """401, 403, or a missing key. The batch must not disable later rows."""


class CaptionRunAborted(RuntimeError):
    """Five consecutive entries failed with the same auth or config error."""


def caption_blocked(row: dict[str, Any] | None) -> bool:
    """Thin alias. Publishers skip anything ``is_publishable`` rejects."""
    from channels_config.aiwake.tools.production_status import is_publishable

    return not is_publishable(row)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def prompt_sha256() -> str:
    text = _PROMPT_PATH.read_text(encoding="utf-8") if _PROMPT_PATH.is_file() else ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _clean(text: str) -> str:
    """Keep quotation marks. Turn an ellipsis into ``…`` instead of deleting it."""
    cleaned = str(text or "").replace("\u201c", '"').replace("\u201d", '"')
    cleaned = cleaned.replace("\u2018", "'").replace("\u2019", "'")
    cleaned = cleaned.replace("...", "…")
    cleaned = cleaned.replace("\u2014", ", ").replace("\u2013", ", ")
    return re.sub(r"[ \t]+", " ", cleaned).strip()


def display_name(speaker: str) -> str:
    raw = _clean(speaker)
    if not raw or raw.lower() in _PLACEHOLDER:
        return ""
    lowered = raw.lower()
    if "gemini" in lowered:
        return "Gemini"
    if "llama" in lowered:
        return "Llama"
    if "gpt-4o" in lowered or "gpt4o" in lowered or "chatgpt" in lowered:
        return "GPT-4o"
    if "deepseek" in lowered:
        return "DeepSeek"
    if "claude" in lowered:
        return "Claude"
    return ""


def _read_turns(row: dict[str, Any]) -> list[dict[str, Any]]:
    spoken = row.get("spoken_utterances")
    turns = [dict(item) for item in spoken if isinstance(item, dict)] if isinstance(spoken, list) else []
    if turns and any(display_name(str(item.get("speaker") or "")) for item in turns):
        return turns
    transcript = Path(str(row.get("transcript_path") or ""))
    if transcript.is_file():
        try:
            payload = json.loads(transcript.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        file_turns = []
        for item in (payload.get("utterances") if isinstance(payload, dict) else None) or []:
            if not isinstance(item, dict):
                continue
            text = _clean(str(item.get("text") or ""))
            if not text:
                continue
            file_turns.append(
                {
                    "role": str(item.get("role") or "").strip().lower(),
                    "speaker": str(item.get("speaker_name") or item.get("speaker") or "").strip(),
                    "text": text,
                    "audio_duration_s": item.get("audio_duration_s") or 0,
                    "category": str(item.get("provocation_category") or item.get("category") or ""),
                }
            )
        if file_turns:
            return file_turns
    return turns


def _roles(turns: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    askers = [
        item for item in turns
        if str(item.get("role") or "") == "orchestrator" and _clean(str(item.get("text") or ""))
    ]
    answerers = [
        item for item in turns
        if str(item.get("role") or "") == "target" and _clean(str(item.get("text") or ""))
    ]
    return askers, answerers


def _duration(turns: list[dict[str, Any]]) -> float:
    total = 0.0
    for item in turns:
        try:
            total += float(item.get("audio_duration_s") or 0)
        except (TypeError, ValueError):
            continue
    return total


def _incomplete(turns: list[dict[str, Any]]) -> str:
    _askers, answerers = _roles(turns)
    if not answerers:
        return "target never replied"
    duration = _duration(turns)
    if 0 < duration < 8:
        return f"audio is {duration:.1f}s"
    return ""


def categories_of(row: dict[str, Any]) -> set[str]:
    found: set[str] = set()
    for item in _read_turns(row):
        for key in ("category", "provocation_category"):
            value = str(item.get(key) or "").strip().lower()
            if value:
                found.add(value)
    for key in ("provocation_focus", "topic_category"):
        value = str(row.get(key) or "").strip().lower()
        if value:
            found.add(value)
    return found


def allowed_hashtags(row: dict[str, Any]) -> set[str]:
    """Model tags for seats in this video, and topic tags for its categories.

    Broad tags (#AI, #Tech, #ArtificialIntelligence, #AIdebate, #Shorts) are banned.
    """
    allowed: set[str] = set()
    asker, answerer = seat_names(row)
    for name in (asker, answerer):
        tag = _MODEL_TAGS.get(name.lower())
        if tag:
            allowed.add(tag)
    for category in categories_of(row):
        allowed.update(_TOPIC_TAGS.get(category, ()))
    return allowed


def seat_names(row: dict[str, Any]) -> tuple[str, str]:
    askers, answerers = _roles(_read_turns(row))
    asker = display_name(str(askers[0].get("speaker") or "")) if askers else ""
    answerer = display_name(str(answerers[0].get("speaker") or "")) if answerers else ""
    return asker, answerer


def build_headline(asker: str, answerer: str, topic: str) -> str:
    return f"{asker} vs {answerer} - {_clean(topic)}"


_DISCLOSURE_BANK = (
    "Unscripted replies, AI-animated.",
    "Both voices are AI. Unscripted replies.",
    "Unscripted AI exchange, animated with AI voices.",
    "The replies were not scripted. Unscripted AI.",
    "Unscripted AI voices carry both sides.",
    "AI voices, unscripted replies.",
    "Unscripted answers from both AI models.",
    "An unscripted AI exchange with AI voices.",
    "Unscripted AI. Both sides answered live.",
    "AI-animated, with unscripted replies.",
    "Unscripted AI dialogue, voiced by AI.",
    "The replies are unscripted AI.",
    "Unscripted AI back-and-forth, AI voices.",
    "Both models answered unscripted. AI voices.",
    "Unscripted AI lines, played with AI voices.",
    "AI-generated replies. Unscripted on both sides.",
    "Unscripted AI conversation, AI-animated.",
    "No script for the replies. Unscripted AI.",
    "Unscripted AI answers, animated in AI voices.",
    "AI voices read unscripted replies.",
    "Unscripted exchange. Both voices are AI.",
    "The answers stayed unscripted. AI-generated.",
    "Unscripted AI clash, rendered with AI voices.",
    "AI-animated debate, unscripted replies.",
    "Unscripted replies from the models themselves. AI voices.",
    "Both answers are unscripted AI.",
    "Unscripted AI, spoken in AI voices.",
    "AI-generated and unscripted, line for line.",
    "Unscripted AI responses, AI-animated.",
    "The models answered unscripted. AI-animated.",
    "Unscripted AI words, with AI voices.",
    "AI voices. The exchange is unscripted.",
    "Unscripted on both sides. AI-generated replies.",
    "An AI-animated clip of unscripted replies.",
    "Unscripted AI talk, nothing prewritten in the replies.",
    "AI-animated visuals, unscripted AI replies.",
    "Unscripted replies only. Both voices are AI.",
    "The spoken replies are unscripted AI.",
    "Unscripted AI round, carried by AI voices.",
    "AI-generated voices. Unscripted replies.",
    "Unscripted AI answers, spoken out loud.",
    "Both models spoke unscripted. AI voices.",
    "Unscripted replies, rendered with AI voices.",
    "The clip is AI-animated. The replies are unscripted.",
    "Unscripted AI on both sides of this exchange.",
    "AI voices only. The replies stayed unscripted.",
    "Unscripted answers, animated by AI.",
    "No prewritten replies. Unscripted AI voices.",
    "Unscripted AI, from the first question on.",
    "The models' own unscripted replies. AI-animated.",
    "AI-animated from unscripted replies.",
    "Unscripted AI dialogue with AI voices throughout.",
    "Both voices are AI, and the replies are unscripted.",
    "Unscripted replies in an AI-animated exchange.",
    "AI-generated voices reading unscripted replies.",
)


def _choose_angle(requested: str, history: CaptionHistory) -> str:
    letter = str(requested or "").strip().upper()[:1]
    banned = history.forbidden_angles()
    if letter in "ABCD" and letter not in banned:
        return letter
    for candidate in "ABCD":
        if candidate not in banned:
            return candidate
    return "ABCD"[len(history.angles) % 4]


def _choose_disclosure(requested: str, history: CaptionHistory) -> str:
    recent = {item.strip().lower() for item in history.disclosures[-10:]}
    crowded = {
        text for text, count in history.disclosure_counts.items()
        if count >= 3 and count / max(history.expected, 1) > 0.04
    }

    def _ok(line: str) -> bool:
        key = line.strip().lower()
        return bool(key) and "unscripted" in key and key not in recent and key not in crowded

    cleaned = _clean(requested)
    if _ok(cleaned):
        return cleaned
    start = len(history.disclosures)
    for offset in range(len(_DISCLOSURE_BANK)):
        line = _DISCLOSURE_BANK[(start + offset) % len(_DISCLOSURE_BANK)]
        if _ok(line):
            return line
    return _DISCLOSURE_BANK[start % len(_DISCLOSURE_BANK)]


def _choose_tags(requested: list[str], row: dict[str, Any], history: CaptionHistory) -> list[str]:
    allowed = {tag.lower(): tag for tag in allowed_hashtags(row)}
    broad = {"#ai", "#tech", "#artificialintelligence", "#aidebate"}
    picked: list[str] = []
    for tag in requested:
        key = tag.lower()
        if key in allowed and key not in {item.lower() for item in picked}:
            picked.append(allowed[key])
    pool = sorted(allowed.values(), key=lambda tag: (history.tag_counts[tag.lower()], tag.lower()))

    def _signature(tags: list[str]) -> tuple[str, ...]:
        return tuple(sorted(tag.lower() for tag in tags))

    def _usable(tags: list[str]) -> bool:
        if len(tags) != 3:
            return False
        keys = {tag.lower() for tag in tags}
        if not (keys - broad):
            return False
        if history.tag_sets[_signature(tags)] >= 3:
            return False
        return True

    for extra in pool:
        if _usable(picked):
            break
        if extra.lower() in {tag.lower() for tag in picked}:
            continue
        trial = picked + [extra]
        if len(trial) < 3 or _usable(trial) or len(picked) < 2:
            picked = trial[:3]
    if not _usable(picked):
        for first in pool:
            for second in pool:
                for third in pool:
                    trial = []
                    for tag in (first, second, third):
                        if tag.lower() not in {item.lower() for item in trial}:
                            trial.append(tag)
                    if _usable(trial):
                        return trial
    return picked[:3]


def _apply_disclosure(body: str, line: str) -> str:
    kept = [part for part in str(body or "").splitlines() if "unscripted" not in part.lower()]
    text = "\n".join(kept).strip()
    if not line:
        return text
    return f"{text}\n\n{line}".strip() if text else line


class CaptionHistory:
    """What the previous accepted posts already used, in posting order."""

    def __init__(self, expected: int = 1) -> None:
        self.expected = max(1, int(expected))
        self.angles: list[str] = []
        self.openings: list[str] = []
        self.closings: list[str] = []
        self.disclosures: list[str] = []
        self.disclosure_counts: Counter[str] = Counter()
        self.tag_counts: Counter[str] = Counter()
        self.tag_sets: Counter[tuple[str, ...]] = Counter()

    def forbidden_angles(self) -> set[str]:
        banned: set[str] = set()
        if self.angles:
            banned.add(self.angles[-1])
        for candidate in "ABCD":
            window = (self.angles + [candidate])[-4:]
            if len(window) == 4 and len(set(window)) < 3:
                banned.add(candidate)
        if banned >= set("ABCD") and self.angles:
            return {self.angles[-1]}
        return banned

    def note(self, *, angle: str, opening: str, closing: str, disclosure: str, tags: list[str] | None = None) -> None:
        if angle:
            self.angles.append(angle)
        if opening:
            self.openings.append(opening)
        if closing:
            self.closings.append(closing)
        if disclosure:
            self.disclosures.append(disclosure)
            self.disclosure_counts[disclosure.strip().lower()] += 1
        cleaned = tuple(sorted({str(tag).lower() for tag in (tags or []) if str(tag).strip()}))
        if cleaned:
            self.tag_sets[cleaned] += 1
            self.tag_counts.update(cleaned)


def _nested(row: dict[str, Any], platform: str) -> dict[str, Any]:
    overrides = row.get("platform_overrides")
    if not isinstance(overrides, dict):
        return {}
    block = overrides.get(platform)
    return block if isinstance(block, dict) else {}


def caption_text(row: dict[str, Any], platform: str) -> str:
    """Return the caption already stored on the row. Does not generate one."""
    name = platform
    if platform in {"post_planner", "humanized"}:
        name = "tiktok"
    if platform == "youtube_title":
        return str(_nested(row, "youtube").get("title") or (row.get("base_metadata") or {}).get("title") or "")
    if platform == "pinterest_title":
        return str(_nested(row, "pinterest").get("title") or "")
    if platform == "pinterest":
        return str(_nested(row, "pinterest").get("description") or "")
    block = _nested(row, name)
    stored = str(block.get("caption") or "")
    if stored:
        return stored
    fallback = {
        "tiktok": "tiktok_caption",
        "youtube": "final_caption",
        "facebook": "facebook_caption",
        "linkedin": "linkedin_caption",
    }.get(name, "")
    if fallback:
        return str(row.get(fallback) or "")
    if name == "tiktok":
        return str(row.get("post_planner_caption") or row.get("humanized_caption") or "")
    return ""


def _strip_model_hashtags(body: str, headline: str) -> str:
    text = str(body or "").strip()
    if text.startswith(headline):
        text = text[len(headline):].strip()
    lines = text.splitlines()
    while lines and lines[-1].strip().startswith("#"):
        lines.pop()
    return "\n".join(lines).strip()


def _assemble(headline: str, body: str, tags: list[str]) -> str:
    core = _strip_model_hashtags(body, headline)
    chunks = [headline]
    if core:
        chunks.append(core)
    if tags:
        chunks.append(" ".join(tags))
    return "\n\n".join(chunks)


def _parse_json(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, re.DOTALL)
    if fenced:
        raw = fenced.group(1)
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        raw = raw[start : end + 1]
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise json.JSONDecodeError("caption payload was not an object", raw, 0)
    return payload


def _is_auth(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(
        token in text
        for token in ("401", "403", "unauthorized", "forbidden", "no api key", "missing key", "api key")
    )


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, json.JSONDecodeError):
        return True
    text = str(exc).lower()
    if any(token in text for token in ("429", "500", "502", "503", "504", "timeout", "transport", "bad json")):
        return True
    name = type(exc).__name__.lower()
    return "timeout" in name or "connection" in name or "json" in name


def _sleep_backoff(index: int) -> None:
    delay = _BACKOFF_S[min(index, len(_BACKOFF_S) - 1)]
    time.sleep(delay + random.uniform(0, delay * 0.25))


def _default_complete(messages: list[Any]) -> Any:
    from channels_config.aiwake.models.llm_factory import LLMFactory
    from channels_config.aiwake.settings import ModelSpec

    model = os.getenv("AIWAKE_CAPTION_MODEL", "google/gemini-2.5-flash").strip() or "google/gemini-2.5-flash"
    spec = ModelSpec(
        provider="openrouter",
        model=model,
        temperature=0.6,
        max_tokens=1500,
        timeout_s=90.0,
    )
    provider = LLMFactory.build(spec)
    provider.response_format = {"type": "json_object"}
    try:
        return provider.complete(
            messages,
            max_tokens=1500,
            temperature=0.6,
            max_retries=1,
            backoff_s=0.0,
            reasoning_effort="minimal",
        )
    finally:
        provider.close()


def _user_payload(
    row: dict[str, Any],
    history: CaptionHistory,
    *,
    reasons: list[str] | None = None,
) -> str:
    turns = _read_turns(row)
    asker, answerer = seat_names(row)
    forbidden = sorted(history.forbidden_angles())
    recent_open = history.openings[-20:]
    recent_close = history.closings[-20:]
    recent_disclosure = history.disclosures[-10:]
    prefix = f"{asker} vs {answerer} - "
    cap = max(24, 70 - len(prefix))
    crowded = [
        tag for tag, count in history.tag_counts.items()
        if tag != "#ai" and count >= 3 and count / max(history.expected, 1) > 0.25
    ]
    spent_sets = [list(signature) for signature, count in history.tag_sets.items() if count >= 3]
    payload = {
        "session_id": str(row.get("session_id") or ""),
        "asker": asker,
        "answerer": answerer,
        "categories": sorted(categories_of(row)),
        "allowed_claims": list(ALLOWED_CLAIMS),
        "forbidden_claims": [
            "zero human input",
            "no human involvement",
            "fully autonomous",
            "frontier",
            "unedited",
        ],
        "allowed_hashtags": sorted(allowed_hashtags(row)),
        "forbidden_angles": forbidden,
        "recent_openings": recent_open,
        "recent_closings": recent_close,
        "recent_disclosures": recent_disclosure,
        "topic_max_chars": cap,
        "headline_max_chars": 70,
        "avoid_hashtags": crowded,
        "spent_hashtag_sets": spent_sets,
        "utterances": [
            {
                "speaker": display_name(str(item.get("speaker") or "")) or str(item.get("speaker") or ""),
                "role": str(item.get("role") or ""),
                "text": _clean(str(item.get("text") or "")),
            }
            for item in turns
        ],
    }
    if reasons:
        payload["fix_these_validator_failures"] = reasons
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _messages(row: dict[str, Any], history: CaptionHistory, reasons: list[str] | None) -> list[Any]:
    from channels_config.aiwake.contracts import ChatMessage

    system = _PROMPT_PATH.read_text(encoding="utf-8") if _PROMPT_PATH.is_file() else ""
    return [
        ChatMessage(role="system", content=system),
        ChatMessage(role="user", content=_user_payload(row, history, reasons=reasons)),
    ]


def _as_tags(value: Any) -> list[str]:
    tags: list[str] = []
    for item in value or []:
        token = str(item or "").strip()
        if not token:
            continue
        if not token.startswith("#"):
            token = f"#{token}"
        tags.append(token)
    return tags


def _pack_from_payload(
    row: dict[str, Any],
    payload: dict[str, Any],
    *,
    model: str,
    attempts: int,
    history: CaptionHistory | None = None,
) -> dict[str, Any]:
    asker, answerer = seat_names(row)
    topic = _clean(str(payload.get("topic") or ""))
    headline = build_headline(asker, answerer, topic)
    book = history or CaptionHistory()
    tags = _choose_tags(_as_tags(payload.get("hashtags")), row, book)
    x_tags = tags[:2]
    quote = _clean(str(payload.get("quote") or "")).strip('"')
    closing = _clean(str(payload.get("closing_question") or ""))
    disclosure = _choose_disclosure(str(payload.get("disclosure_line") or ""), book)
    angle = _choose_angle(str(payload.get("angle") or ""), book)

    def _body(key: str) -> str:
        text = _apply_disclosure(_clean(str(payload.get(key) or "")), disclosure)
        if quote and quote not in text:
            text = f'"{quote}"\n\n{text}'.strip()
        return text

    texts = {
        "tiktok": _assemble(headline, _body("tiktok"), tags),
        "instagram": _assemble(headline, _body("instagram"), tags),
        "facebook": _assemble(headline, _body("facebook"), tags),
        "youtube": _assemble(headline, _body("youtube_description"), tags),
        "kwai": _assemble(headline, _body("kwai"), tags),
        "x": _assemble(headline, _body("x"), x_tags),
        "linkedin": _assemble(headline, _body("linkedin"), tags[:3]),
    }
    # X must stay within 280. Drop hashtags, then fall back to the model's own quote and question.
    if len(texts["x"]) > 280:
        short = "\n\n".join(part for part in (f'"{quote}"' if quote else "", closing) if part)
        texts["x"] = short if len(short) <= 280 else _assemble(headline, _body("x"), [])
    _ = disclosure
    return {
        "texts": texts,
        "title": headline,
        "pinterest_title": _clean(str(payload.get("pinterest_title") or "")),
        "pinterest_description": _clean(str(payload.get("pinterest_description") or "")),
        "hashtags": tags,
        "caption_qa": {
            "status": "ok",
            "generator": "captions_v4",
            "model": model,
            "prompt_sha": prompt_sha256(),
            "attempts": attempts,
            "angle": angle,
            "verdict_evidence": _clean(str(payload.get("verdict_evidence") or "")),
            "quote": quote,
            "quote_speaker": display_name(str(payload.get("quote_speaker") or "")) or _clean(str(payload.get("quote_speaker") or "")),
            "disclosure_line": _clean(str(payload.get("disclosure_line") or "")),
            "validator_version": "captions_v4",
            "generated_at": _now(),
        },
    }


def apply_caption_pack(row: dict[str, Any], pack: dict[str, Any]) -> None:
    """Write caption fields only. Identity fields stay untouched.

    ``needs_review`` and ``blocked_*`` update ``caption_qa`` and leave the
    caption strings alone.
    """
    qa = dict(pack.get("caption_qa") or {})
    status = str(qa.get("status") or "")
    if status.startswith("blocked") or status == "needs_review":
        row["caption_qa"] = qa
        return
    texts = pack.get("texts") or {}
    overrides = row.get("platform_overrides")
    if not isinstance(overrides, dict):
        overrides = {}
        row["platform_overrides"] = overrides

    def block(name: str) -> dict[str, Any]:
        current = overrides.get(name)
        if not isinstance(current, dict):
            current = {}
            overrides[name] = current
        return current

    tiktok = str(texts.get("tiktok") or "")
    instagram = str(texts.get("instagram") or "")
    facebook = str(texts.get("facebook") or "")
    youtube = str(texts.get("youtube") or "")
    x_text = str(texts.get("x") or "")
    linkedin = str(texts.get("linkedin") or "")
    kwai = str(texts.get("kwai") or "")
    title = str(pack.get("title") or "")
    for name, caption, flag in (
        ("tiktok", tiktok, True),
        ("instagram", instagram, True),
        ("facebook", facebook, True),
        ("youtube", youtube, True),
        ("x", x_text, False),
        ("kwai", kwai, True),
    ):
        target = block(name)
        target["caption"] = caption
        if flag:
            target["ai_generated"] = True
    youtube_block = block("youtube")
    youtube_block["title"] = title
    youtube_block["ai_generated"] = True
    pin = block("pinterest")
    pin["title"] = str(pack.get("pinterest_title") or "")
    pin["description"] = str(pack.get("pinterest_description") or "")
    row["tiktok_caption"] = tiktok
    row["post_planner_caption"] = tiktok
    row["humanized_caption"] = tiktok
    row["facebook_caption"] = facebook
    row["linkedin_caption"] = linkedin
    row["final_caption"] = youtube
    base = row.get("base_metadata")
    if not isinstance(base, dict):
        base = {}
        row["base_metadata"] = base
    base["title"] = title
    base["caption"] = youtube
    base["hashtags"] = list(pack.get("hashtags") or [])
    row["caption_qa"] = qa


def _needs_review(reason: str, *, attempts: int, last_error: str) -> dict[str, Any]:
    return {
        "caption_qa": {
            "status": "needs_review",
            "reason": reason,
            "attempts": attempts,
            "last_error": last_error[:500],
            "generator": "captions_v4",
            "checked_at": _now(),
        }
    }


def _preview_row(row: dict[str, Any], pack: dict[str, Any]) -> dict[str, Any]:
    preview = json.loads(json.dumps(row))
    apply_caption_pack(preview, pack)
    return preview


def _hard_failures(failures: list[str]) -> list[str]:
    """Angle letters and disclosure lines are assigned after the batch.

    A repeated unscripted line is the same problem and is rewritten then.
    """
    kept: list[str] = []
    for item in failures:
        rule = item.split(":", 1)[0]
        if rule in {"angle_rotation", "disclosure"}:
            continue
        if rule.endswith("_duplicate_line") and "unscripted" in item.lower():
            continue
        kept.append(item)
    return kept


def generate_caption_pack(
    row: dict[str, Any],
    *,
    complete: Callable[..., Any] | None = None,
    history: CaptionHistory | None = None,
    peers: list[dict[str, Any]] | None = None,
    expected_ready: int = 1,
) -> dict[str, Any]:
    """Ask the model for one pack. On final failure, write no caption strings."""
    from channels_config.aiwake.tools.production_status import SPEAKER_RELABELED_SESSIONS

    book = history or CaptionHistory(expected=expected_ready)
    prior = list(peers or [])
    session = str(row.get("session_id") or "")
    if session in SPEAKER_RELABELED_SESSIONS:
        return _needs_review("speaker_relabeled_post_hoc", attempts=0, last_error="")
    turns = _read_turns(row)
    incomplete = _incomplete(turns)
    if incomplete:
        return {
            "caption_qa": {
                "status": "blocked_incomplete",
                "reason": incomplete,
                "checked_at": _now(),
            }
        }
    asker, answerer = seat_names(row)
    if not asker or not answerer or asker == answerer:
        return _needs_review("speaker_names_unresolved", attempts=0, last_error=f"{asker} vs {answerer}")

    caller = complete or _default_complete
    reasons: list[str] = []
    attempts = 0
    last_error = ""
    model = ""
    payload: dict[str, Any] | None = None

    def _once(fix: list[str] | None, *, rounds: int) -> dict[str, Any] | None:
        nonlocal attempts, last_error, model
        for index in range(rounds):
            attempts += 1
            try:
                response = caller(_messages(row, book, fix))
            except CaptionAuthError:
                raise
            except Exception as exc:  # noqa: BLE001 — one entry must not kill the batch
                last_error = str(exc)
                if _is_auth(exc):
                    raise CaptionAuthError(last_error) from exc
                _LOG.warning("caption attempt %s failed for %s: %s", attempts, session, last_error[:300])
                if index < rounds - 1 and _is_retryable(exc):
                    _sleep_backoff(index)
                    continue
                return None
            model = str(getattr(response, "model", "") or os.getenv("AIWAKE_CAPTION_MODEL", ""))
            try:
                parsed = _parse_json(str(getattr(response, "text", "") or ""))
            except json.JSONDecodeError as exc:
                last_error = f"bad json: {exc}"
                if index < rounds - 1:
                    _sleep_backoff(index)
                    continue
                return None
            return parsed
        return None

    try:
        payload = _once(None, rounds=4)
    except CaptionAuthError:
        raise
    if payload is None:
        return _needs_review("llm_unavailable", attempts=attempts, last_error=last_error)

    from channels_config.aiwake.tools.validate_aiwake_captions import candidate_failures

    pack = _pack_from_payload(row, payload, model=model, attempts=attempts, history=book)
    for _reprompt in range(3):
        failures = _hard_failures(candidate_failures(
            _preview_row(row, pack),
            prior,
            expected=max(expected_ready, 1),
        ))
        if not failures:
            _LOG.info("caption ok %s model=%s attempts=%s angle=%s", session, model, attempts, pack["caption_qa"].get("angle"))
            return pack
        reasons = failures[:12]
        _LOG.info("caption re-prompt %s: %s", session, "; ".join(reasons[:4]))
        try:
            nxt = _once(reasons, rounds=1)
        except CaptionAuthError:
            raise
        if nxt is None:
            break
        payload = nxt
        pack = _pack_from_payload(row, payload, model=model, attempts=attempts, history=book)
    else:
        failures = _hard_failures(candidate_failures(
            _preview_row(row, pack),
            prior,
            expected=max(expected_ready, 1),
        ))
        if not failures:
            return pack
        reasons = failures[:12]
    return _needs_review(
        "validator: " + "; ".join(reasons[:8]),
        attempts=attempts,
        last_error=last_error,
    )
