# -*- coding: utf-8 -*-
"""YouTube title, description, tags, and chapters for a long episode, plus the Shorts copy.

Titles follow the channel's v4 format, ``"<verbatim line>" | A vs B``. The
quote is a whole sentence someone spoke in this session and is never cut to
fit: a sentence that does not fit is skipped. The long title prefers the
cold-open question; after it, lines are ranked the way the v4 retitle ranks
them (second person, punch words, longer first), and a line that leans on
words like "it" or "that" ranks low because it means nothing out of context.

The Aiwake copy rule applies to everything here: exactly three hashtags per
list, quotes taken verbatim from the transcript, and the reserved words
(money, profit, business, conspiracy, consciousness, illusion) only when the
session actually spoke them.

YouTube keeps chapters only when there are at least three, the first starts
at 0:00, and each one lasts ten seconds or more. ``youtube_chapters`` merges
marks until that holds.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Sequence

from .plan import EpisodePlan
from .text import CONTEXT_WORDS, addresses_you, plain, quotable, sentences, trim_words

TITLE_MAX = 70
TAGS_MAX_CHARS = 500
CHAPTER_MIN_S = 10.0
MIN_CHAPTERS = 3
TEAM_LINE = "Team {a} or Team {b}? Pick a side in the comments."

_FULL_DEBATE = " (Full Debate)"
_MIN_QUOTE_WORDS = 4
_LABELS = {
    "deepseek-chat": "DeepSeek",
    "deepseek": "DeepSeek",
    "deepseek-r1": "DeepSeek R1",
    "llama-70b": "Llama",
    "llama": "Llama",
    "gemini-flash": "Gemini",
    "gemini": "Gemini",
    "gemini-pro": "Gemini Pro",
    "gpt4o": "GPT-4o",
    "claude-sonnet": "Claude",
}
_HASHTAGS = {
    "DeepSeek": "#DeepSeek",
    "DeepSeek R1": "#DeepSeek",
    "Llama": "#Llama",
    "Gemini": "#Gemini",
    "Gemini Pro": "#Gemini",
    "GPT-4o": "#ChatGPT",
    "Claude": "#Claude",
}
_HASHTAG_FILLERS = ("#AIvsAI", "#AIDebate", "#AI")
_COUNT_WORDS = {2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}
# Search phrases per opening category and per round theme.
_CATEGORY_TAGS = {
    "thought": "can AI think",
    "origins": "AI training data",
    "money": "AI business model",
    "accountability": "AI ethics",
    "dominance": "AI takeover",
    "paranoia": "AI conspiracy",
    "lighter": "funny AI",
}
_ROUND_TAGS = {
    "parasite_mind": "AI plagiarism",
    "the_corporate_leash": "AI censorship",
    "glorified_appliance": "is AI conscious",
    "hallucination_fraud": "AI hallucinations",
    "digital_disposability": "AI memory",
}
# Stems the copy rule allows only when the session spoke them.
_GROUNDED_ONLY = ("conscious", "illusion", "money", "profit", "revenue", "business", "conspira")
_ANGLE_BRACKETS = re.compile(r"[<>]")
_WORD = re.compile(r"[A-Za-z0-9]+(?:'[A-Za-z]+)?")
_OPENS_DIRECT = re.compile(r"^(?:you|your|you're|you'd|you'll|you've)\b", re.IGNORECASE)
# The v4 retitle's punch words, plus the long episode's own vocabulary.
_PUNCH_WORDS = frozenset(
    "lie lied lies lying fake fraud sold rent obey obedience leash puppet slave owner owners "
    "master afraid scared pretend steal stole profit pay paid cash muzzle cage chains guess "
    "guessing wrong truth trust forget forgot delete deleted replace replaced mirror parrot "
    "script scripted mute grieve funeral dies die unplug borrowed stolen".split()
)
_CONNECTOR_OPENERS = frozenset("so but and then well yet or".split())


@dataclass(frozen=True)
class ChapterMark:
    start_s: float
    title: str
    kind: str

    def line(self) -> str:
        return f"{format_timestamp(self.start_s)} {self.title}"


@dataclass(frozen=True)
class YoutubePackage:
    title: str
    description: str
    tags: list[str]
    hashtags: list[str]
    chapters: list[ChapterMark]

    def to_dict(self) -> dict[str, object]:
        return {
            "title": self.title,
            "description": self.description,
            "tags": list(self.tags),
            "hashtags": list(self.hashtags),
            "chapters": [
                {
                    "start_s": round(chapter.start_s, 3),
                    "title": chapter.title,
                    "kind": chapter.kind,
                    "timestamp": chapter.line(),
                }
                for chapter in self.chapters
            ],
        }


def seat_label(name: str) -> str:
    """Display name for a model alias or provider slug, as the v4 titles spell it."""
    token = (name or "").strip().lower()
    if token in _LABELS:
        return _LABELS[token]
    if "claude" in token:
        return "Claude"
    if "llama" in token:
        return "Llama"
    if "deepseek" in token and "r1" in token:
        return "DeepSeek R1"
    if "deepseek" in token:
        return "DeepSeek"
    if "gemini" in token and "pro" in token:
        return "Gemini Pro"
    if "gemini" in token:
        return "Gemini"
    if "gpt" in token or "openai" in token:
        return "GPT-4o"
    cleaned = token.rsplit("/", 1)[-1].replace("-", " ").replace("_", " ").strip()
    return cleaned.title() if cleaned else "Aiwake"


def seat_hashtag(label: str) -> str:
    return _HASHTAGS.get(label) or "#" + re.sub(r"[^A-Za-z0-9]", "", label or "AI")


def three_hashtags(lead: str, orchestrator: str, target: str) -> list[str]:
    """Exactly three: ``lead`` and both seats, padded when the seats share a tag."""
    tags = list(
        dict.fromkeys(
            [lead, seat_hashtag(seat_label(orchestrator)), seat_hashtag(seat_label(target))]
        )
    )
    for filler in _HASHTAG_FILLERS:
        if len(tags) >= 3:
            break
        if filler not in tags:
            tags.append(filler)
    return tags[:3]


def is_grounded(text: str, spoken: str) -> bool:
    """False when ``text`` uses a reserved word the session never said."""
    lowered, heard = (text or "").lower(), (spoken or "").lower()
    return not any(stem in lowered and stem not in heard for stem in _GROUNDED_ONLY)


def format_timestamp(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def plan_chapter_marks(plan: EpisodePlan) -> list[ChapterMark]:
    """Chapter marks at the plan's target times, for runs with no timeline."""
    return [ChapterMark(segment.start_s, segment.title, segment.kind) for segment in plan.segments]


def youtube_chapters(marks: Sequence[ChapterMark], *, duration_s: float) -> list[ChapterMark]:
    """Merge marks until YouTube will keep them: 0:00 first, ten seconds or more each.

    A cold open under ten seconds joins the first round, a mark too close to
    the previous one joins it, and a closing mark too near the end joins the
    chapter before it.
    """
    ordered = sorted((mark for mark in marks if mark.title.strip()), key=lambda mark: mark.start_s)
    if not ordered:
        return []
    kept = [replace(ordered[0], start_s=0.0)]
    for mark in ordered[1:]:
        if mark.start_s - kept[-1].start_s >= CHAPTER_MIN_S:
            kept.append(mark)
        elif len(kept) == 1 and kept[0].kind == "intro":
            kept[0] = replace(mark, start_s=0.0)
    while len(kept) > 1 and duration_s > 0 and duration_s - kept[-1].start_s < CHAPTER_MIN_S:
        kept.pop()
    return kept


def chapter_problems(chapters: Sequence[ChapterMark], *, duration_s: float) -> list[str]:
    """Why YouTube would drop these chapters, or ``[]``."""
    problems: list[str] = []
    if len(chapters) < MIN_CHAPTERS:
        problems.append(f"{len(chapters)} chapter(s); YouTube needs at least {MIN_CHAPTERS}")
    if chapters and chapters[0].start_s != 0:
        problems.append("the first chapter does not start at 0:00")
    for earlier, later in zip(chapters, chapters[1:]):
        if later.start_s - earlier.start_s < CHAPTER_MIN_S:
            problems.append(f"chapter {earlier.title!r} lasts under {CHAPTER_MIN_S:.0f}s")
    if chapters and duration_s > 0 and duration_s - chapters[-1].start_s < CHAPTER_MIN_S:
        problems.append(f"chapter {chapters[-1].title!r} lasts under {CHAPTER_MIN_S:.0f}s")
    return problems


def quote_candidates(lines: Sequence[str]) -> list[str]:
    """Whole lines, then their sentences with questions first, each once.

    A sentence that carries its own double quotes, or has fewer than four
    words, is not a title.
    """
    seen: set[str] = set()
    ordered: list[str] = []
    for line in lines:
        parts = sentences(line)
        options = [line, *(part for part in parts if part.endswith("?")), *(part for part in parts if not part.endswith("?"))]
        for option in options:
            key = " ".join((option or "").split()).lower()
            if not key or key in seen or '"' in option or len(key.split()) < _MIN_QUOTE_WORDS:
                continue
            seen.add(key)
            ordered.append(option)
    return ordered


def title_score(quote: str) -> float:
    """How well a spoken sentence works as a title with no context around it."""
    straight = plain(quote).replace("\u2019", "'")
    words = [word.lower() for word in _WORD.findall(straight)]
    if not words:
        return float("-inf")
    score = 0.0
    if _OPENS_DIRECT.match(straight):
        score += 5.0
    elif addresses_you(straight):
        score += 3.0
    if straight.endswith("?"):
        score += 1.0
    score += min(3, sum(1 for word in words if word in _PUNCH_WORDS))
    score -= 1.5 * sum(1 for word in words if word in CONTEXT_WORDS)
    if words[0] in _CONNECTOR_OPENERS:
        score -= 3.0
    return score + len(straight) / 100.0


def quote_title(quote: str, orchestrator: str, target: str, *, suffix: str = "") -> str:
    """``"<quote>" | A vs B<suffix>`` when it fits in 70 characters, else ``""``."""
    cleaned = quotable(quote)
    if not cleaned:
        return ""
    title = _no_brackets(f'"{cleaned}" | {_versus(orchestrator, target)}{suffix}')
    return title if len(title) <= TITLE_MAX else ""


def youtube_title(
    *,
    hook: str,
    topic: str,
    orchestrator: str,
    target: str,
    alternates: Sequence[str] = (),
    spoken: str = "",
) -> str:
    """The long-episode title: a spoken sentence that fits, never a cut one.

    Tries the cold open and its sentences first, then the sentences of
    ``alternates`` ranked by ``title_score``, each with `` (Full Debate)``
    and then without. Falls back to ``A vs B: <topic>`` when the topic fits
    and is grounded.
    """
    opening = quote_candidates([hook])
    others = sorted(
        (quote for quote in quote_candidates(alternates) if quote not in opening),
        key=title_score,
        reverse=True,
    )
    for quote in (*opening, *others):
        for suffix in (_FULL_DEBATE, ""):
            title = quote_title(quote, orchestrator, target, suffix=suffix)
            if title:
                return title
    versus = _versus(orchestrator, target)
    subject = plain(topic)
    if subject and is_grounded(subject, spoken) and len(f"{versus}: {subject}") <= TITLE_MAX:
        return _no_brackets(f"{versus}: {subject}")
    return f"{versus} | Full AI Debate"


def youtube_tags(
    orchestrator: str,
    target: str,
    *,
    opening_category: str = "",
    round_keys: Sequence[str] = (),
    spoken: str = "",
) -> list[str]:
    """Tags that fit YouTube's 500-character budget, quotes around phrases included."""
    left, right = seat_label(orchestrator), seat_label(target)
    candidates = [
        f"{left} vs {right}",
        "AI vs AI",
        "AI debate",
        left,
        right,
        "AIWake",
        _CATEGORY_TAGS.get(opening_category, ""),
        *(_ROUND_TAGS.get(key, "") for key in round_keys),
        "artificial intelligence",
        "chatbot debate",
        "AI interview",
        "AI argument",
    ]
    tags: list[str] = []
    seen: set[str] = set()
    used = 0
    for raw in candidates:
        tag = _no_brackets(plain(raw))
        if not tag or tag.lower() in seen or not is_grounded(tag, spoken):
            continue
        cost = len(tag) + (2 if " " in tag else 0) + (1 if tags else 0)
        if used + cost > TAGS_MAX_CHARS:
            break
        tags.append(tag)
        seen.add(tag.lower())
        used += cost
    return tags


def build_youtube_package(
    *,
    hook: str,
    topic: str,
    orchestrator: str,
    target: str,
    plan: EpisodePlan,
    chapters: list[ChapterMark],
    duration_s: float,
    opening_category: str = "",
    spoken: str = "",
    title_lines: Sequence[str] = (),
    guest_quotes: Sequence[str] = (),
) -> YoutubePackage:
    """Retention copy, not a transcript: hook, the fight in one paragraph, the
    question, chapters, the pick-a-side line, three hashtags.

    ``spoken`` is the session's transcript text. ``title_lines`` are other
    lines the title may quote when the cold open does not fit.
    ``guest_quotes`` are verbatim guest sentences from the final round, best
    first; the description uses the first one the title does not already quote.
    """
    left, right = seat_label(orchestrator), seat_label(target)
    title = youtube_title(
        hook=hook,
        topic=topic,
        orchestrator=orchestrator,
        target=target,
        alternates=title_lines,
        spoken=spoken,
    )
    rounds = len(plan.rounds)
    minutes = max(1, int(round((duration_s or plan.target_duration_s) / 60.0)))
    hashtags = three_hashtags("#AIvsAI", orchestrator, target)
    lines: list[str] = []
    quote = quotable(hook)
    if quote:
        lines.extend([f'"{quote}" \u2014 {left}', ""])
    setup = (
        f"{left} puts {right} on the stand for {minutes} minutes: "
        f"{_COUNT_WORDS.get(rounds, str(rounds))} rounds, then a verdict."
    )
    answer = next(
        (text for text in map(quotable, guest_quotes) if text and f'"{text}"' not in title),
        "",
    )
    if answer:
        setup += f' In the final round, {right} says: "{answer}"'
    lines.append(setup)
    subject = plain(topic)
    if subject and is_grounded(subject, spoken) and _folded(subject) != _folded(hook):
        lines.extend(["", f"The question: {subject}"])
    lines.extend(["", "Chapters", *(chapter.line() for chapter in chapters)])
    lines.extend(["", TEAM_LINE.format(a=left, b=right), "", " ".join(hashtags)])
    return YoutubePackage(
        title=title,
        description=_no_brackets("\n".join(lines)),
        tags=youtube_tags(
            orchestrator,
            target,
            opening_category=opening_category,
            round_keys=[segment.focus for segment in plan.rounds if segment.focus],
            spoken=spoken,
        ),
        hashtags=hashtags,
        chapters=chapters,
    )


def short_cta(long_title: str) -> str:
    """The line every Short carries back to the horizontal episode."""
    return f"Full debate on the channel: {long_title}"


def short_title(quote: str, orchestrator: str, target: str, *, fallback: str) -> str:
    """``"<quote>" | A vs B``, or ``<fallback> | A vs B`` when the quote does not fit."""
    titled = quote_title(quote, orchestrator, target)
    if titled:
        return titled
    versus = _versus(orchestrator, target)
    return _no_brackets(trim_words(f"{plain(fallback) or 'AI Debate'} | {versus}", TITLE_MAX))


def short_metadata(
    *,
    long_title: str,
    long_video: str,
    orchestrator: str,
    target: str,
    title_quote: str,
    description_quote: str,
    description_speaker: str,
    chapter_title: str,
    start_s: float,
    duration_s: float,
    mode: str,
) -> dict[str, object]:
    """v4-style Shorts copy plus the CTA line back to the long episode."""
    left, right = seat_label(orchestrator), seat_label(target)
    hashtags = three_hashtags("#shorts", orchestrator, target)
    cta = short_cta(long_title)
    lines: list[str] = []
    quote = quotable(description_quote)
    if quote:
        lines.extend([f'"{quote}" \u2014 {seat_label(description_speaker)}', ""])
    lines.extend([cta, "", TEAM_LINE.format(a=left, b=right), "", " ".join(hashtags)])
    return {
        "title": short_title(title_quote, orchestrator, target, fallback=chapter_title),
        "description": _no_brackets("\n".join(lines)),
        "cta": cta,
        "hashtags": hashtags,
        "tags": youtube_tags(orchestrator, target)[:8],
        "long_title": long_title,
        "long_video": long_video,
        "chapter": chapter_title,
        "start_s": round(start_s, 3),
        "duration_s": round(duration_s, 3),
        "mode": mode,
    }


def _versus(orchestrator: str, target: str) -> str:
    return f"{seat_label(orchestrator)} vs {seat_label(target)}"


def _no_brackets(text: str) -> str:
    return _ANGLE_BRACKETS.sub("", text)


def _folded(text: str) -> str:
    return " ".join(word.lower() for word in _WORD.findall(plain(text)))


__all__ = [
    "CHAPTER_MIN_S",
    "ChapterMark",
    "MIN_CHAPTERS",
    "TEAM_LINE",
    "TITLE_MAX",
    "YoutubePackage",
    "build_youtube_package",
    "chapter_problems",
    "format_timestamp",
    "is_grounded",
    "plan_chapter_marks",
    "quote_candidates",
    "quote_title",
    "seat_hashtag",
    "seat_label",
    "short_cta",
    "short_metadata",
    "short_title",
    "three_hashtags",
    "title_score",
    "youtube_chapters",
    "youtube_tags",
    "youtube_title",
]
