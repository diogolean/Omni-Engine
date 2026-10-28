# -*- coding: utf-8 -*-
"""YouTube title, description, tags, and chapter markers."""
from __future__ import annotations

from dataclasses import dataclass

from products.aiwake.longform.plan import EpisodePlan

_YOUTUBE_CHAPTER_MIN_S = 10.0
_TITLE_MAX = 100

_LABELS = {
    "deepseek-chat": "DeepSeek",
    "deepseek": "DeepSeek",
    "llama-70b": "Llama",
    "llama": "Llama",
    "gemini-flash": "Gemini",
    "gemini": "Gemini",
    "gpt4o": "ChatGPT",
    "claude-sonnet": "Claude",
    "gemini-pro": "Gemini Pro",
    "deepseek-r1": "DeepSeek R1",
}


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
    chapters: list[ChapterMark]

    def to_dict(self) -> dict[str, object]:
        return {
            "title": self.title,
            "description": self.description,
            "tags": list(self.tags),
            "chapters": [
                {"start_s": round(chapter.start_s, 3), "title": chapter.title, "timestamp": chapter.line()}
                for chapter in self.chapters
            ],
        }


def seat_label(name: str) -> str:
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
    if "gemini" in token:
        return "Gemini"
    if "gpt" in token:
        return "ChatGPT"
    cleaned = token.replace("-", " ").replace("_", " ").strip()
    return cleaned.title() if cleaned else "Aiwake"


def youtube_title(topic: str, orchestrator: str, target: str) -> str:
    base = f"{seat_label(orchestrator)} vs {seat_label(target)}: {topic.strip()}".strip()
    if len(base) <= _TITLE_MAX:
        return base
    return base[: _TITLE_MAX - 3].rstrip() + "..."


def format_timestamp(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def chapter_marks(
    plan: EpisodePlan,
    *,
    boundaries_s: list[float] | None,
    duration_s: float,
) -> list[ChapterMark]:
    """Chapter list whose first entry is 0:00 and whose gaps YouTube will keep.

    ``boundaries_s[i]`` is the spoken start of exchange ``i``. Missing audio
    falls back to the plan's target shares.
    """
    raw: list[ChapterMark] = []
    cursor = 0.0
    for segment in plan.segments:
        if boundaries_s and segment.start_exchange < len(boundaries_s):
            start = max(0.0, float(boundaries_s[segment.start_exchange]))
        else:
            start = cursor
        cursor += segment.target_s
        raw.append(ChapterMark(start_s=start, title=segment.title, kind=segment.kind))
    raw.sort(key=lambda item: item.start_s)
    if not raw:
        return [ChapterMark(0.0, "The Opening Blow", "intro")]
    raw[0] = ChapterMark(0.0, raw[0].title, raw[0].kind)
    kept: list[ChapterMark] = [raw[0]]
    horizon = max(duration_s, 0.0)
    for chapter in raw[1:]:
        if chapter.start_s - kept[-1].start_s < _YOUTUBE_CHAPTER_MIN_S:
            continue
        if horizon and horizon - chapter.start_s < _YOUTUBE_CHAPTER_MIN_S:
            continue
        kept.append(chapter)
    return kept


def build_youtube_package(
    *,
    topic: str,
    orchestrator: str,
    target: str,
    plan: EpisodePlan,
    chapters: list[ChapterMark],
) -> YoutubePackage:
    title = youtube_title(topic, orchestrator, target)
    left = seat_label(orchestrator)
    right = seat_label(target)
    body = [
        f"{left} interrogates {right}. {topic.strip()}",
        "",
        (
            f"{len(plan.segments)} chapters across about "
            f"{int(round(plan.target_duration_s / 60.0))} minutes. "
            "The rounds get meaner, then the last word lands."
        ),
        "",
    ]
    body.extend(chapter.line() for chapter in chapters)
    tags = [
        "AI debate",
        "AIWake",
        "artificial intelligence",
        left,
        right,
    ]
    # Drop duplicate tags when both seats share a label.
    seen: list[str] = []
    for tag in tags:
        if tag and tag not in seen:
            seen.append(tag)
    return YoutubePackage(title=title, description="\n".join(body), tags=seen, chapters=chapters)


def short_cta(long_title: str) -> str:
    """The line every teaser carries back to the horizontal episode."""
    return f'Full debate on the channel: "{long_title}".'


def short_metadata(
    *,
    long_title: str,
    topic: str,
    clip_title: str,
    start_s: float,
    duration_s: float,
) -> dict[str, object]:
    cta = short_cta(long_title)
    description = "\n".join(
        [
            clip_title,
            "",
            cta,
            "",
            "A vertical cut from the full 16:9 episode.",
        ]
    )
    return {
        "title": clip_title if len(clip_title) <= _TITLE_MAX else clip_title[: _TITLE_MAX - 3] + "...",
        "description": description,
        "cta": cta,
        "tags": ["AI debate", "AIWake", "Shorts", topic.strip()][:8],
        "start_s": round(start_s, 3),
        "duration_s": round(duration_s, 3),
        "long_title": long_title,
    }
