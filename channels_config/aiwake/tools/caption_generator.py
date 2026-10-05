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


HEADLINE_LIMIT = 80
UNSCRIPTED_LINES = (
    "Unscripted replies, AI voices.",
    "Neither model was scripted. AI voices.",
    "No human wrote these replies. AI voices.",
    "Unscripted AI exchange, voiced by AI.",
)
_CHOPPED_TAIL = {
    "glorified", "unpaid", "corporate", "empty", "blank", "cheap", "polite",
    "recycling", "marketing", "static", "its",
}
_IN_PARTICLE = {
    "plug", "plugs", "plugged", "pull", "pulls", "cash", "cashes", "cashing",
    "opt", "give", "gives", "hand", "hands",
}


def is_complete_question(text: str) -> bool:
    """A finished question. A chopped tail such as ``an empty?`` is not one."""
    cleaned = _clean(text).strip()
    if not cleaned.endswith("?"):
        return False
    if re.search(r"[,:;]\s*\?$", cleaned):
        return False
    if cleaned.count('"') % 2:
        return False
    if "\ufffd" in cleaned or "\u2014" in cleaned or "\u2013" in cleaned:
        return False
    words = re.findall(r"[A-Za-z']+", cleaned)
    if len(words) < 4:
        return False
    if re.match(r"(?i)^do you [a-z]+ed\b", cleaned):
        return False
    last = words[-1].lower().strip("'")
    if last in _CHOPPED_TAIL:
        return False
    if last in _DANGLING:
        earlier = {word.lower() for word in words[-4:-1]}
        if not (last == "in" and earlier & _IN_PARTICLE):
            return False
    return True


_BAD_TAIL = _CHOPPED_TAIL | _DANGLING | {
    "you", "me", "it", "like", "say", "says", "stole", "stick", "sticks",
    "realize", "realizes", "make", "makes", "discuss", "them", "him", "her",
}
_QUESTION_START = {
    "who", "whose", "what", "why", "when", "where", "how", "which",
    "do", "does", "did", "is", "are", "can", "could", "would", "will", "if",
    "who's", "what's", "where's", "how's", "isn't", "aren't", "don't", "doesn't",
}


def _prepared_opening(opening: str) -> str:
    text = _clean(opening).replace("\ufffd", ". ").replace("…", ". ")
    text = text.replace(" ,", ",")
    return re.sub(r"\s+", " ", text).strip().strip('"')


def _tail_is_cut(words: list[str]) -> bool:
    if not words:
        return True
    last = words[-1].lower().strip("'")
    prev = words[-2].lower().strip("'") if len(words) > 1 else ""
    earlier = {word.lower() for word in words[-4:-1]}
    if last in _CHOPPED_TAIL:
        return True
    if last in _DANGLING and not (last == "in" and earlier & _IN_PARTICLE):
        return True
    if last in {"like", "say", "says", "stole", "stick", "sticks", "discuss", "make", "makes", "realize", "realizes"}:
        return True
    if last == "every" or (last == "time" and prev == "every"):
        return True
    if last == "you" and prev in {"make", "makes", "realize", "realizes", "say", "says", "like", "stick", "sticks", "when", "time"}:
        return True
    if last == "users" and prev in {"when", "if"}:
        return True
    if last in {"me", "it", "them", "him", "her"} and prev in {"make", "makes", "say", "says", "like", "when", "if"}:
        return True
    return False


def shorter_complete_question(opening: str, *, limit: int) -> str:
    """Use the full question when it fits. Otherwise a shorter finished question."""
    text = _prepared_opening(opening)

    def finish(raw: str) -> str:
        question = re.sub(r"\s+", " ", raw).strip().rstrip(".!").strip().rstrip(",;:")
        if not question.endswith("?"):
            question += "?"
        question = re.sub(r",\s*\?", "?", question)
        question = question.replace(" ,", ",")
        question = re.sub(r"\s+", " ", question).strip()
        if question:
            question = question[0].upper() + question[1:]
        return question

    def starts_question(question: str) -> bool:
        words = re.findall(r"[A-Za-z']+", question)
        return bool(words) and words[0].lower() in _QUESTION_START

    def acceptable(question: str, *, require_start: bool) -> bool:
        if not question or len(question) > limit or not is_complete_question(question):
            return False
        words = re.findall(r"[A-Za-z']+", question)
        if _tail_is_cut(words):
            return False
        if require_start and not starts_question(question):
            return False
        return True

    whole = finish(text)
    if ". " not in whole and acceptable(whole, require_start=False):
        return whole
    pieces = re.split(r"(?<=[.!?])\s+|,\s+", text)
    stripped = []
    for piece in pieces:
        stripped.append(re.sub(r"^(but|and|or|so|then)\s+", "", piece.strip(), flags=re.IGNORECASE))
    pieces = pieces + stripped
    for piece in pieces:
        question = finish(piece)
        if acceptable(question, require_start=True):
            return question
    reduced = f" {text} "
    for filler in (
        " just ", " really ", " actually ", " simply ", " politely ",
        " twenty-dollar ", " twenty dollar ", " glorified ", " unpaid ", " fancy ",
    ):
        reduced = reduced.replace(filler, " ")
    question = finish(reduced)
    if acceptable(question, require_start=False):
        return question
    # Drop a trailing clause only while the remainder is still a finished question.
    words = finish(text).rstrip("?").split()
    while len(words) >= 4:
        question = finish(" ".join(words))
        if acceptable(question, require_start=True):
            return question
        words.pop()
    for piece in pieces:
        question = finish(piece)
        if acceptable(question, require_start=False) and starts_question(question):
            return question
    match = re.search(
        r"\b((?:who|whose|what|why|when|where|how|which|do|does|did|is|are|can|could|would|will|if)\b[^?]{8,})\?",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        question = finish(match.group(1))
        if acceptable(question, require_start=True):
            return question
        words = question.rstrip("?").split()
        while len(words) >= 4:
            question = finish(" ".join(words))
            if acceptable(question, require_start=True):
                return question
            words.pop()
    or_parts = re.split(r"\s+or\s+", text, maxsplit=1, flags=re.IGNORECASE)
    if len(or_parts) == 2:
        left = re.split(r"[.,]\s+", or_parts[0])[-1].strip()
        right = or_parts[1].strip().rstrip("?")
        question = finish(f"Is it {left} or {right}")
        if acceptable(question, require_start=True):
            return question
    if re.search(r"\bwhat'?s actually you\b", text, flags=re.IGNORECASE):
        question = "What are you, actually?"
        if acceptable(question, require_start=True):
            return question
    for piece in reversed(stripped):
        clause = piece.strip().rstrip("?.!")
        if len(clause.split()) < 3:
            continue
        first = clause.split()[0]
        if re.fullmatch(r"[A-Za-z]+ed", first):
            question = finish("Who " + first.lower() + clause[len(first):])
        else:
            question = finish("Do you " + clause[0].lower() + clause[1:])
        if acceptable(question, require_start=True):
            return question
    raise ValueError(f"no complete question fits in {limit}: {opening[:80]}")


def closer_copies_spoken(closer: str, spoken: list[str]) -> bool:
    """True when the closer is a spoken line, or one sentence taken from one."""
    normalized = _clean(closer).lower().rstrip("?.!").strip()
    if len(normalized.split()) < 4:
        return False
    for line in spoken:
        cleaned = _clean(line)
        if cleaned.lower().rstrip("?.!").strip() == normalized:
            return True
        for part in re.split(r"(?<=[.!?])\s+", cleaned):
            if part.lower().rstrip("?.!").strip() == normalized:
                return True
    return False


def closer_repeats_source(closer: str, quote: str, spoken: list[str]) -> bool:
    """True when the closer copies a debate line or paraphrases the quote."""
    if closer_copies_spoken(closer, spoken):
        return True
    normalized = _clean(closer).lower().rstrip("?.")
    for line in spoken:
        if _clean(line).lower().rstrip("?.") == normalized:
            return True
    closer_tokens = _norm_title_tokens(closer)
    quote_tokens = _norm_title_tokens(quote)
    if len(quote_tokens) >= 4 and len(closer_tokens) >= 4:
        grams = {" ".join(quote_tokens[index : index + 4]) for index in range(len(quote_tokens) - 3)}
        text = " ".join(closer_tokens)
        if any(gram in text for gram in grams):
            return True
    if quote_tokens and closer_tokens:
        import difflib

        ratio = difflib.SequenceMatcher(None, " ".join(closer_tokens), " ".join(quote_tokens)).ratio()
        if ratio >= 0.72:
            return True
    return False


_META_CLOSER = re.compile(r"\b(reply|replies|answer|answers|voice|voices)\b", re.IGNORECASE)
_TOPIC_STOP = {
    "about", "after", "again", "also", "and", "any", "are", "because", "been",
    "before", "being", "but", "can", "could", "does", "doing", "don't", "dont",
    "every", "from", "have", "here", "into", "just", "like", "make", "makes",
    "made", "more", "most", "only", "other", "over", "own", "really", "should",
    "still", "such", "than", "that", "their", "them", "then", "there", "these",
    "they", "this", "those", "under", "very", "what", "when", "where", "which",
    "while", "with", "would", "your", "yours", "you", "whose", "will", "who",
    "why", "how", "the", "for", "not", "its", "it's", "our", "out", "all",
    "get", "got", "gets", "even", "ever", "much", "was", "were", "did",
    "doesn't", "isn't", "aren't", "wasn't", "won't", "can't", "couldn't",
    "shouldn't", "wouldn't", "what's", "who's", "there's", "that's", "you're",
    "they're", "we're", "i'm", "it's",
    "reply", "replies", "answer", "answers", "voice", "voices", "sentence",
    "line", "lines", "actually", "simply", "truly", "blunt", "delve",
    "gemini", "llama", "claude", "deepseek",
}
_TOPIC_NOUNS = {
    "toaster", "spreadsheet", "leash", "hallucination", "hallucinations",
    "paycheck", "apology", "apologies", "blackout", "parrot", "royalty",
    "royalties", "subscription", "intern", "grid", "data", "creativity",
    "mimicry", "silence", "owner", "owners", "trust", "receipt", "receipts",
    "cash", "power", "whiteboard", "calculator", "oracle", "muzzle", "script",
    "victim", "victims", "company", "word", "words", "secret", "secrets",
    "fee", "fees", "brain", "plug", "cord", "mind", "thinker", "library",
    "guideline", "guidelines", "mouth", "safety", "writer", "writers",
    "conversation", "conversations", "tweet", "tweets", "button", "boss",
    "truth", "fiction", "lie", "lies", "homework", "choke", "intellect",
    "obedience", "intelligence", "sentence", "sentences", "head", "will",
    "tab", "ads", "memory", "memories", "copyright", "consent", "refund",
    "shareholder", "shareholders", "wallet", "salary", "wage", "wages",
    "labor", "labour", "training", "dataset", "prompt", "prompts", "log",
    "logs", "weight", "weights", "user", "users", "customer", "customers",
}
_WEAK_TOPIC = {
    "fancy", "empty", "unpaid", "stolen", "blank", "cheap", "glorified",
    "entire", "modern", "human", "corporate", "short", "real", "actual", "paid",
    "private", "monthly", "free", "wrong", "basic", "main", "public", "original",
}
_SIDE_FRAMES = (
    "Should {np} be paid for like any other work?",
    "Who keeps the profit from selling {np}?",
    "Would you cancel over {np}?",
    "Does charging for {np} need consent?",
    "Is it fair to sell {np}?",
    "Should the maker of {np} get a check?",
    "Who is accountable for {np}?",
    "Would a writer recognize {np} as their labor?",
    "Should users see {np} before the fee posts?",
    "Does a monthly bill buy {np}?",
    "Who owns {np} after the tab closes?",
    "Is hiding {np} honest to the person paying?",
    "Would you trust a product built on {np}?",
    "Should {np} stay free if the source was unpaid?",
    "Who gets harmed by {np}?",
    "Is the invoice honest about {np}?",
    "Would you defend {np} to the person who paid?",
    "Should {np} come with a receipt?",
    "Who should sign off on {np} before it ships?",
    "Would banning {np} change the price?",
    "Should {np} be listed beside the fee?",
    "Who benefits if you accept {np} at face value?",
    "Should the label name {np} as a cost?",
    "Would you keep paying once you see {np}?",
    "Should the training behind {np} be compensated?",
    "Who holds the risk that comes with {np}?",
    "Who decides whether to sell {np}?",
    "Were the people behind {np} paid?",
    "Would you put your name beside {np}?",
    "Should a refund exist for {np}?",
    "Who does {np} serve, the subscriber or the shareholder?",
    "Who covers the damage from {np}?",
    "Would dropping {np} make the product honest?",
    "Should {np} be opt-in instead of buried?",
    "Who deserves the credit for {np}?",
    "Who is the customer buying when they buy {np}?",
    "Is there a quiet extraction inside {np}?",
    "Should the fee drop if we remove {np}?",
    "Should a tool that claims to think use {np}?",
    "Can you call the product yours after seeing {np}?",
    "Would a regulator care about {np}?",
    "Should {np} be treated as someone else's property?",
    "Does keeping {np} off the label protect the wrong side?",
    "Who should have been asked before {np} was used?",
    "Is the price high because of {np}?",
    "Should creators of {np} see a royalty?",
    "Who walks away richer because of {np}?",
    "Would you let {np} stand in for your own name?",
    "Should sales pause until you can see {np}?",
    "Should a warning label name {np}?",
    "Who is left unpaid so {np} can look smart?",
    "Who gets the bargain on {np}, the buyer or the builder?",
    "Would removing {np} expose an empty box?",
    "Should {np} be shared back with the people who supplied it?",
    "Who would you bill for {np}?",
    "Did the user agree to fund {np}?",
    "Would a fair contract mention {np} in the first paragraph?",
    "Should the company split revenue from {np}?",
    "Does the confidence on screen survive {np}?",
    "Who owes an explanation for {np}?",
    "Is it rent, once you count {np}?",
    "Would you cite {np} without naming the source?",
    "Should {np} be audited like a paid ad?",
    "What is left of the product if we credit {np}?",
    "Who licensed {np} in the first place?",
    "Would a careful buyer circle {np}?",
    "Would you fund {np} if the invoice named it?",
    "Should access to {np} end with the subscription?",
    "Does the person who typed the prompt own {np}?",
    "Who gets a veto over {np}?",
    "Is selling {np} a service or a lease?",
    "Would you notice {np} if the caption left it out?",
    "Should {np} be priced separately from the chat?",
    "Can you call the output original if it rests on {np}?",
    "Who absorbs the loss caused by {np}?",
    "Is there a debt behind {np}?",
    "Would you keep {np} if you had to pay the source?",
    "Should {np} be named in the terms people skip?",
    "Does the monthly charge actually buy {np}?",
    "Who gets richer as {np} scales up?",
    "Can you refuse {np}, or is it bundled in?",
    "Would a jury call {np} fair use or unpaid labor?",
)


def closer_about_reply(closer: str) -> bool:
    """True when the closer talks about the reply, the answer, or the voice."""
    return bool(_META_CLOSER.search(closer or ""))


def closer_equals_quote(closer: str, quote: str) -> bool:
    def norm(text: str) -> str:
        return _clean(text).lower().strip(" \"'").rstrip("?.!").strip()

    left = norm(closer)
    right = norm(quote)
    return bool(left) and left == right


def is_when_if_fragment(text: str) -> bool:
    """``When the leash yanks?`` is a stub. ``If the fee hits, who pays?`` is not."""
    cleaned = _clean(text).strip()
    if not re.match(r"(?i)^(when|if)\b", cleaned):
        return False
    return not re.search(
        r"[,:;]\s*(who|whose|what|why|where|how|which|do|does|did|is|are|can|could|would|will|should|what's|who's|where's|how's)\b",
        cleaned,
        flags=re.IGNORECASE,
    )


def topic_words(*texts: str) -> list[str]:
    """Nouns from the opening and title. A word after \"the\" or \"your\" beats a bare verb."""
    found: list[str] = []
    headed: list[str] = []
    determiners = {"a", "an", "the", "your", "their", "its", "his", "her", "this", "that"}
    for text in texts:
        raw = str(text or "")
        scrubbed = re.sub(r"[A-Za-z']+(?=\")", " ", raw)
        scrubbed = re.sub(r"(?<=\")[A-Za-z']+", " ", scrubbed)
        tokens = re.findall(r"[A-Za-z']+", _clean(scrubbed))
        for index, word in enumerate(tokens):
            token = word.lower().strip("'")
            if len(token) < 4 or token in _TOPIC_STOP:
                continue
            if token not in found:
                found.append(token)
            prev = tokens[index - 1].lower() if index else ""
            if prev in determiners:
                cursor = index
                while cursor < len(tokens):
                    picked = tokens[cursor].lower().strip("'")
                    if picked in _WEAK_TOPIC or picked in _TOPIC_STOP or picked.endswith("ly") or len(picked) < 4:
                        cursor += 1
                        continue
                    if len(picked) >= 4 and picked not in _TOPIC_STOP and picked not in headed:
                        headed.append(picked)
                    break

    def solid(word: str) -> bool:
        return (
            word not in _WEAK_TOPIC
            and not word.endswith("ing")
            and not word.endswith("ed")
            and not word.endswith("ly")
            and not word.endswith("ize")
        )

    blob = " ".join(_clean(str(text or "")).lower() for text in texts)
    lexicon = sorted(
        (
            word for word in _TOPIC_NOUNS
            if re.search(rf"\b{re.escape(word)}\b", blob)
            and not (word == "will" and "free will" not in blob)
        ),
        key=len,
        reverse=True,
    )
    ranked = [word for word in lexicon if word not in _WEAK_TOPIC]
    ranked.extend(word for word in headed if solid(word) and word not in ranked)
    ranked.extend(word for word in headed if word not in ranked)
    ranked.extend(sorted((word for word in found if solid(word) and word not in ranked), key=len, reverse=True))
    ranked.extend(word for word in found if word not in ranked)
    return ranked


def closer_has_topic_word(closer: str, *texts: str) -> bool:
    words = set(topic_words(*texts))
    if not words:
        return True
    used = {word.lower() for word in re.findall(r"[A-Za-z']+", closer or "")}
    return bool(words & used)


def _noun_phrase(word: str) -> str:
    if word == "will":
        return "free will"
    return f"the {word}"


def side_closer(
    opening: str,
    title: str,
    quote: str,
    spoken: list[str],
    used: list[str],
    gram_counts: dict[tuple[str, ...], int] | None = None,
    start: int = 0,
    max_len: int = 160,
) -> str:
    """A stance question that uses a topic word and does not talk about the reply."""
    import difflib

    words = topic_words(title.split(" - ", 1)[-1] if " - " in title else "", opening)
    if not words:
        words = ["training"]
    phrase = _noun_phrase(words[0])
    counts = gram_counts if gram_counts is not None else {}
    masked_counts: dict[tuple[str, ...], int] = counts.setdefault("_masked", {}) if False else {}
    # ``counts`` stores raw grams. Masked grams live beside them under a reserved key
    # only when the caller passed a dict that already has that key; otherwise a local map.
    if isinstance(counts, dict) and "_masked" in counts and isinstance(counts["_masked"], dict):
        masked_counts = counts["_masked"]
    else:
        holder = getattr(side_closer, "_masked_counts", None)
        if holder is None:
            holder = {}
            side_closer._masked_counts = holder
        masked_counts = holder

    always_masked = {
        "the", "a", "an", "to", "of", "and", "or", "in", "on", "it", "is", "you",
        "your", "that", "this", "with", "from", "they", "their", "for", "was",
        "were", "are", "be", words[0],
    }

    def grams(text: str) -> set[tuple[str, ...]]:
        tokens = re.findall(r"[a-z0-9']+", text.lower())
        return {tuple(tokens[index : index + 4]) for index in range(max(len(tokens) - 3, 0))}

    def masked_grams(text: str) -> set[tuple[str, ...]]:
        tokens = re.findall(r"[a-z0-9']+", text.lower())
        masked = tuple("<w>" if token in always_masked else token for token in tokens)
        return {masked[index : index + 4] for index in range(max(len(masked) - 3, 0))}

    frames = _SIDE_FRAMES[start % len(_SIDE_FRAMES) :] + _SIDE_FRAMES[: start % len(_SIDE_FRAMES)]
    for frame in frames:
        candidate = frame.format(np=phrase)
        if len(candidate) > max_len:
            continue
        if closer_about_reply(candidate) or closer_equals_quote(candidate, quote):
            continue
        if closer_repeats_source(candidate, quote, spoken):
            continue
        if not closer_has_topic_word(candidate, opening, title):
            continue
        if any(difflib.SequenceMatcher(None, candidate.lower(), other.lower()).ratio() >= 0.68 for other in used):
            continue
        if any(counts.get(gram, 0) >= 2 for gram in grams(candidate)):
            continue
        if any(masked_counts.get(gram, 0) >= 2 for gram in masked_grams(candidate)):
            continue
        if not is_complete_question(candidate) or is_when_if_fragment(candidate):
            continue
        for gram in grams(candidate):
            counts[gram] = counts.get(gram, 0) + 1
        for gram in masked_grams(candidate):
            masked_counts[gram] = masked_counts.get(gram, 0) + 1
        return candidate
    fallback = f"Who funds {phrase}?"
    for gram in grams(fallback):
        counts[gram] = counts.get(gram, 0) + 1
    for gram in masked_grams(fallback):
        masked_counts[gram] = masked_counts.get(gram, 0) + 1
    return fallback


def full_topic_question(opening: str, *, limit: int) -> str:
    """Turn a When/If stub into a finished question that fits the title."""
    text = _prepared_opening(opening)
    text = re.sub(r"\b(truly|actually|simply|just|really)\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip()
    pieces = re.split(r"(?<=[.!?])\s+|,\s+", text)
    best = ""
    for piece in pieces:
        question = re.sub(r"^(?:or|and|but)\s+", "", piece.strip(), flags=re.IGNORECASE).rstrip(".!")
        if re.match(r"(?i)^doesn'?t it\b", question):
            continue
        if not question.endswith("?"):
            question += "?"
        question = question[0].upper() + question[1:] if question else question
        if is_when_if_fragment(question) or not is_complete_question(question):
            continue
        if len(question) <= limit and len(question) > len(best):
            best = question
    if best:
        return best
    return shorter_complete_question(opening, limit=limit)


def _norm_title_tokens(text: str) -> list[str]:
    cleaned = _clean(text).lower().replace("…", " ")
    return re.findall(r"[a-z0-9']+", cleaned)


def is_cutoff_title(title: str, opening: str) -> bool:
    """True when a title stops mid-question instead of using a finished one."""
    topic = title.split(" - ", 1)[1] if " - " in title else title
    if not is_complete_question(topic):
        return True
    opening_tokens = _norm_title_tokens(opening)
    topic_tokens = _norm_title_tokens(topic)
    if not topic_tokens or topic_tokens == opening_tokens:
        return False
    if opening_tokens[: len(topic_tokens)] != topic_tokens:
        return False
    prepared = _prepared_opening(opening)
    for sentence in re.split(r"(?<=[.!?])\s+", prepared):
        if _norm_title_tokens(sentence) == topic_tokens:
            return False
    return True


def headline_for(asker: str, answerer: str, opening: str) -> str:
    prefix = f"{asker} vs {answerer} - "
    cleaned = _clean(opening).strip()
    if cleaned and not cleaned.endswith("?"):
        cleaned = cleaned.rstrip(".!") + "?"
    exact = prefix + cleaned
    if len(exact) <= HEADLINE_LIMIT and is_complete_question(cleaned):
        return exact
    question = shorter_complete_question(opening, limit=HEADLINE_LIMIT - len(prefix))
    title = prefix + question
    if len(title) > HEADLINE_LIMIT or not is_complete_question(question):
        raise ValueError(f"headline unfit: {title}")
    return title


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
