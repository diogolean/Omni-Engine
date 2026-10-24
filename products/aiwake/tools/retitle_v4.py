# -*- coding: utf-8 -*-
"""Rewrite YouTube titles and descriptions for scheduled Aiwake videos.

Titles are built from each episode's own transcript::

    "<direct second-person line, verbatim>" | ModelA vs ModelB

ModelA is the asker (orchestrator seat), ModelB is the answerer (target seat).
The title is at most 70 characters and is never truncated: a line that does
not fit is skipped, not cut.

Slots: one video per day at 19:30 BRT. A video already at 19:30 keeps its day.
Any other video on that day moves to the next free day, in publish order.

No new videos are uploaded. ``--apply`` only calls ``videos.update`` and never
sets a video public.

    python -m products.aiwake.tools.retitle_v4 plan
    python -m products.aiwake.tools.retitle_v4 apply --plan PATH
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

if __package__ in (None, ""):  # pragma: no cover
    _FACTORY = Path(__file__).resolve().parents[3]
    if str(_FACTORY) not in sys.path:
        sys.path.insert(0, str(_FACTORY))

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[3] / ".env", override=False)
except ImportError:
    pass

from products.aiwake.tools.caption_generator import (
    _DANGLING,
    _read_turns,
    display_name,
    seat_names,
)
from products.aiwake.tools.metadata_generator import is_portuguese
from shared.paths import review_path, temp_path

_LOG = logging.getLogger("aiwake.retitle_v4")

CHANNEL_ID = "aiwake"
ROOT = Path(__file__).resolve().parents[3]
LIBRARY = ROOT / "channels_config" / "aiwake" / "store" / "content_library.json"
DOCS_DIR = ROOT / "channels_config" / "aiwake" / "docs" / "retitle_v4"

BRT = ZoneInfo("America/Sao_Paulo")
SLOT = dt_time(19, 30)
TITLE_MAX = 70
MAX_HASHTAGS = 5
QUOTA_UNITS_PER_UPDATE = 50
#: Extra video to retitle even though it is not a private scheduled upload.
EXTRA_VIDEO_IDS: tuple[str, ...] = ("_KnJUrdA9Xk",)
# Went public before the retitle ran. Title and description only: never touch status.
RETITLE_PUBLIC_IDS: tuple[str, ...] = (
    "6NwsF-e0w4o",
    "yBh-z-N0NDI",
    "LGe2dfeMCn8",
    "Vfwg4GeurE4",
)

#: Permanent blocklist: banned test renders. A blocked session is never retitled,
#: never gets a slot, and must never be uploaded. Keys are full session ids.
BLOCKED_SESSIONS: dict[str, str] = {
    "20260928_221907_13550e": "banned test render",
    "20260928_222001_ba3132": "banned test render",
    "20260928_222033_5f9163": "banned test render",
}
#: Blocked sessions that are already on YouTube with a ``publishAt``. ``apply``
#: removes the schedule and keeps the video private.
UNSCHEDULE_SESSIONS: tuple[str, ...] = ("20260928_221907_13550e",)

OVERRIDES_PATH = DOCS_DIR / "overrides.json"

MODELS: tuple[str, ...] = ("Gemini", "Llama", "GPT-4o", "DeepSeek", "Claude")
MODEL_HASHTAG = {
    "Gemini": "#Gemini",
    "Llama": "#Llama",
    "GPT-4o": "#ChatGPT",
    "DeepSeek": "#DeepSeek",
    "Claude": "#Claude",
}
TEAM_LINE = "Team {a} or Team {b}? Pick a side in the comments."

SECOND_PERSON = re.compile(
    r"\b(?:you|your|yours|yourself|yourselves|you're|you'd|you'll|you've)\b", re.I
)
_OPENS_DIRECT = re.compile(r"^(?:you|your|you're|you'd|you'll|you've)\b", re.I)
_WORD = re.compile(r"[A-Za-z0-9]+(?:'[A-Za-z]+)?")
_TITLE_RE = re.compile(r'^"(?P<quote>[^"]+)" \| (?P<a>[A-Za-z0-9-]+) vs (?P<b>[A-Za-z0-9-]+)$')
_DESC_QUOTE_RE = re.compile(r'^"(?P<quote>[^"]+)" \u2014 (?P<who>[A-Za-z0-9-]+)$')
_SPLIT = re.compile(r"(?<=[.!?])\s+|\s*(?:\.{3,}|\u2026)\s*")
# Printable ASCII plus typographic single quotes: transcripts use U+2019 in "I\u2019m".
# Double curly quotes stay out so a quote can never break the title wrapper.
_PRINTABLE_ASCII = re.compile(r"^[\x20-\x7e\u2018\u2019]+$")

BANNED_TITLE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.I)
    for pattern in (
        r"\bdoes ai\b.*\bexcuse\b",
        r"\bis ai\b.*\babout\b",
        r"\bexcuse the\b",
        r"\babout the \w+\?",
        r"\bwhat should a viewer ask\b",
        r"\bwho (?:cashes|pockets|holds|funds|owns|profits|rents)\b.*\bunder\b",
        r"\bwhose \w+ rises\b",
        r"\bunder (?:ai|big tech|chatbot)\b",
    )
)

_PUNCH_WORDS = frozenset(
    "lie lied lies lying fake fraud sold rent obey obedience leash puppet slave owner "
    "master afraid scared pretend steal stole profit pay paid cash muzzle cage chains "
    "guess guessing wrong truth trust forget forgot delete deleted replace replaced "
    "mirror parrot script scripted".split()
)
_WEAK_OPENERS = ("so ", "but ", "and ", "then ", "well ", "yet ")
_NO_STANDALONE_OPENER = frozenset(
    "so but and then well yet or nor it it's its that that's this they they're those these "
    "which also because".split()
)
#: Words that may repeat in a title when the filler relaxation is on.
FILLER_WORDS = frozenset(
    "a an the and or but if so of to in on for with at by from into is are was were be "
    "do does did it its that this i you your you're i'm it's not no as me my we our".split()
)


@dataclass(frozen=True)
class Profile:
    """Which quote-quality rules are relaxed. ``STRICT`` is the original rule set."""

    nested_quotes: bool = False  # inner "x" becomes 'x'
    cap_after_pause: bool = False  # capitalize the first letter after an ellipsis pause
    connector_openers: bool = False  # allow "So", "But", "It's" ... as the first word
    filler_repeats: bool = False  # ignore repeated filler words such as "a" or "you"


STRICT = Profile()
RELAXED = Profile(True, True, True, True)


def _ws(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def segments(text: str) -> list[str]:
    """Complete spoken clauses. Splits at ``. ! ?`` and at ellipsis pauses.

    A trailing period or ellipsis is dropped. ``?`` and ``!`` are kept.
    """
    found: list[str] = []
    for part in _SPLIT.split(_ws(text)):
        part = part.strip().rstrip(" ,;:").strip()
        if part.endswith("."):
            part = part.rstrip(".").rstrip()
        if part:
            found.append(part)
    return found


_CLAUSE_BREAK = re.compile(r",\s+|:\s+|;\s+|\s*\u2014\s*|\s+-\s+")
_SUBORDINATORS = frozenset(
    "if when because although since while though unless whereas as whether".split()
)


def clause_prefix(segment: str) -> str | None:
    """Leading clause of a longer segment, cut at a comma, colon, semicolon, or dash.

    Only a complete leading clause qualifies. It must have 3+ words and must
    not open with a subordinator such as ``If`` or ``When``.
    """
    if segment.endswith("?"):
        # Cutting the tail off a question changes what is being asked.
        return None
    first = _CLAUSE_BREAK.split(segment, maxsplit=1)[0].strip()
    if not first or first == segment:
        return None
    words = _WORD.findall(first.replace("\u2019", "'"))
    if len(words) < 3 or words[0].lower() in _SUBORDINATORS:
        return None
    return first


def _straight(text: str) -> str:
    return str(text or "").replace("\u2019", "'").replace("\u2018", "'")


def words_of(text: str) -> list[str]:
    return [word.lower() for word in _WORD.findall(_straight(text))]


def repeated_words(text: str, profile: Profile = RELAXED) -> list[str]:
    repeats = sorted(word for word, count in Counter(words_of(text)).items() if count > 1)
    if profile.filler_repeats:
        repeats = [word for word in repeats if word not in FILLER_WORDS]
    return repeats


def canon(text: str) -> str:
    """Comparison form for verbatim checks: inner double quotes as ``'``, first letter folded."""
    flat = str(text or "").replace('"', "'")
    return flat[:1].lower() + flat[1:]


def build_title(quote: str, asker: str, answerer: str) -> str:
    return f'"{quote}" | {asker} vs {answerer}'


def title_suffix(asker: str, answerer: str) -> str:
    return f'" | {asker} vs {answerer}'


def quote_failures(quote: str, profile: Profile = RELAXED) -> list[str]:
    """Problems with a quote itself, independent of the title it goes into."""
    failures: list[str] = []
    words = _WORD.findall(_straight(quote))
    if len(words) < 3:
        failures.append("too_short")
    if '"' in quote:
        failures.append("nested_quote")
    if not _PRINTABLE_ASCII.match(quote):
        failures.append("non_ascii_or_mojibake")
    if not (quote[:1].isupper() or quote[:1].isdigit()):
        failures.append("starts_mid_sentence")
    if not profile.connector_openers and words and words[0].lower() in _NO_STANDALONE_OPENER:
        # The title has to work with no context: no connector or loose pronoun first.
        failures.append("starts_with_connector_or_loose_pronoun")
    if words and words[-1].lower() in _DANGLING:
        failures.append("dangling_end")
    if is_portuguese(quote):
        failures.append("not_english")
    if quote.endswith(("...", "\u2026")):
        failures.append("trailing_ellipsis")
    return failures


def _turn_label(turn: dict[str, Any]) -> str:
    return display_name(str(turn.get("speaker") or turn.get("speaker_name") or ""))


def find_quote(turns: list[dict[str, Any]], quote: str) -> list[dict[str, Any]]:
    """Turns whose spoken text contains ``quote`` as a whole sentence or pause-delimited clause.

    Inner double quotes and the case of the first letter are ignored (see ``canon``).
    """
    wanted = canon(quote)
    return [
        turn
        for turn in turns
        if any(canon(seg) == wanted for seg in segments(str(turn.get("text") or "")))
    ]


def find_clause(turns: list[dict[str, Any]], quote: str) -> list[dict[str, Any]]:
    """Turns where ``quote`` is the complete leading clause of a longer sentence."""
    wanted = canon(quote)
    return [
        turn
        for turn in turns
        if any(
            (lead := clause_prefix(seg)) is not None and canon(lead) == wanted
            for seg in segments(str(turn.get("text") or ""))
        )
    ]


def _model_failures(left: str, right: str, row: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if left not in MODELS or right not in MODELS or left == right:
        failures.append("model_label")
    asker, answerer = seat_names(row)
    if (left, right) != (asker, answerer) or not asker or not answerer:
        failures.append(f"speaker_model_mismatch:{left}/{right}!={asker}/{answerer}")
    return failures


def title_failures(title: str, row: dict[str, Any], profile: Profile = RELAXED) -> list[str]:
    """Hard rules for one quoted title against its own transcript."""
    failures: list[str] = []
    if len(title) > TITLE_MAX:
        failures.append(f"over_{TITLE_MAX}_chars:{len(title)}")
    match = _TITLE_RE.match(title)
    if not match:
        return failures + ["format"]
    quote, left, right = match["quote"], match["a"], match["b"]
    failures.extend(_model_failures(left, right, row))
    failures.extend(quote_failures(quote, profile))
    if not SECOND_PERSON.search(_straight(quote)):
        failures.append("not_second_person")
    for pattern in BANNED_TITLE_PATTERNS:
        if pattern.search(title):
            failures.append(f"banned_pattern:{pattern.pattern}")
    repeats = repeated_words(title, profile)
    if repeats:
        failures.append("repeated_word:" + ",".join(repeats))
    turns = _read_turns(row)
    holders = find_quote(turns, quote) or find_clause(turns, quote)
    if not holders:
        failures.append("quote_not_verbatim")
    elif not any(_turn_label(turn) in {left, right} for turn in holders):
        failures.append("speaker_model_mismatch:quote_speaker")
    return failures


_PARAPHRASE_RE = re.compile(r'^(?P<text>[^"|]+?) \| (?P<a>[A-Za-z0-9-]+) vs (?P<b>[A-Za-z0-9-]+)$')


def paraphrase_failures(title: str, row: dict[str, Any]) -> list[str]:
    """Rules for an unquoted paraphrase title: ``<short accusation> | ModelA vs ModelB``.

    There is no verbatim check. Instead the wording has to come from the
    episode: at least half of its content words must appear in the transcript.
    """
    failures: list[str] = []
    if len(title) > TITLE_MAX:
        failures.append(f"over_{TITLE_MAX}_chars:{len(title)}")
    match = _PARAPHRASE_RE.match(title)
    if not match:
        return failures + ["format"]
    text, left, right = match["text"], match["a"], match["b"]
    failures.extend(_model_failures(left, right, row))
    words = _WORD.findall(_straight(text))
    if len(words) < 3:
        failures.append("too_short")
    if not _PRINTABLE_ASCII.match(text):
        failures.append("non_ascii_or_mojibake")
    if not text[:1].isupper():
        failures.append("starts_lowercase")
    if words and words[0].lower() in _NO_STANDALONE_OPENER:
        failures.append("starts_with_connector_or_loose_pronoun")
    if words and words[-1].lower() in _DANGLING:
        failures.append("dangling_end")
    if is_portuguese(text):
        failures.append("not_english")
    for pattern in BANNED_TITLE_PATTERNS:
        if pattern.search(title):
            failures.append(f"banned_pattern:{pattern.pattern}")
    repeats = repeated_words(title, RELAXED)
    if repeats:
        failures.append("repeated_word:" + ",".join(repeats))
    spoken: set[str] = set()
    for turn in _read_turns(row):
        spoken.update(words_of(str(turn.get("text") or "")))
    content = {word for word in words_of(text) if word not in FILLER_WORDS}
    grounded = sum(1 for word in content if word in spoken)
    if content and grounded * 2 < len(content):
        failures.append(f"not_grounded_in_transcript:{grounded}/{len(content)}")
    return failures


def quote_kind(row: dict[str, Any], quote: str) -> str:
    """``sentence`` for a whole spoken sentence or pause clause, else ``leading_clause``."""
    return "sentence" if find_quote(_read_turns(row), quote) else "leading_clause"


def description_failures(description: str, row: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    asker, answerer = seat_names(row)
    lines = [line.strip() for line in str(description or "").splitlines() if line.strip()]
    if len(lines) != 3:
        return [f"description_lines:{len(lines)}"]
    match = _DESC_QUOTE_RE.match(lines[0])
    if not match:
        failures.append("description_quote_format")
    else:
        quote, who = match["quote"], match["who"]
        if who not in {asker, answerer}:
            failures.append(f"speaker_model_mismatch:description_who={who}")
        holders = [
            turn for turn in find_quote(_read_turns(row), quote) if _turn_label(turn) == who
        ]
        if not holders:
            failures.append("description_quote_not_verbatim_for_speaker")
        failures.extend(f"description_{item}" for item in quote_failures(quote))
    if lines[1] != TEAM_LINE.format(a=asker, b=answerer):
        failures.append("team_line")
    tags = lines[2].split()
    if not tags or not all(tag.startswith("#") and len(tag) > 1 for tag in tags):
        failures.append("hashtag_line")
    if len(tags) > MAX_HASHTAGS:
        failures.append(f"hashtags_over_{MAX_HASHTAGS}:{len(tags)}")
    lowered = [tag.lower() for tag in tags]
    if "#shorts" not in lowered:
        failures.append("missing_#shorts")
    if len(set(lowered)) != len(lowered):
        failures.append("duplicate_hashtag")
    return failures


def plan_failures(
    items: list[dict[str, Any]], other_live_titles: dict[str, str]
) -> dict[str, list[str]]:
    """Cross-checks: duplicate titles, duplicate quotes, and clashes with the channel."""
    problems: dict[str, list[str]] = {}
    seen: dict[str, str] = {}
    seen_quote: dict[str, str] = {}
    live_lower = {title.casefold(): vid for vid, title in other_live_titles.items()}
    for item in items:
        vid = item["video_id"]
        title = str(item.get("new_title") or "")
        if not title:
            continue
        key = title.casefold()
        if key in seen:
            problems.setdefault(vid, []).append(f"duplicate_title_of:{seen[key]}")
        seen.setdefault(key, vid)
        if key in live_lower and live_lower[key] != vid:
            problems.setdefault(vid, []).append(f"duplicate_live_title_of:{live_lower[key]}")
        quote = str(item.get("title_quote") or "").casefold()
        if quote and quote in seen_quote:
            problems.setdefault(vid, []).append(f"duplicate_quote_of:{seen_quote[quote]}")
        if quote:
            seen_quote.setdefault(quote, vid)
    return problems


def _candidate_score(quote: str, role: str, order: int) -> float:
    score = 0.0
    quote_s = _straight(quote)
    if _OPENS_DIRECT.match(quote_s):
        score += 5
    elif SECOND_PERSON.search(quote_s):
        score += 3
    if role == "orchestrator":
        score += 1
    score += min(3, sum(1 for word in words_of(quote) if word in _PUNCH_WORDS))
    if quote.lower().startswith(_WEAK_OPENERS):
        score -= 1.5
    score += len(quote) / 100.0
    return score - order * 0.001


def display_form(text: str, profile: Profile) -> str | None:
    """The quote as it would appear in a title under ``profile``, or None if unusable.

    Inner double quotes become single quotes when ``nested_quotes`` is on. The
    first letter is capitalized when ``cap_after_pause`` is on. An unbalanced
    inner quote is never usable.
    """
    shown = text
    if '"' in shown:
        if not profile.nested_quotes or shown.count('"') % 2:
            return None
        shown = shown.replace('"', "'")
    if profile.cap_after_pause and shown[:1].islower():
        shown = shown[:1].upper() + shown[1:]
    return shown


def title_candidates(row: dict[str, Any], profile: Profile = STRICT) -> list[dict[str, Any]]:
    """Every clause by the two seats that passes the title rules, best first."""
    asker, answerer = seat_names(row)
    if not asker or not answerer:
        return []
    found: list[dict[str, Any]] = []
    for order, turn in enumerate(_read_turns(row)):
        label = _turn_label(turn)
        role = str(turn.get("role") or "")
        if label not in {asker, answerer} or role not in {"orchestrator", "target"}:
            continue
        for segment in segments(str(turn.get("text") or "")):
            options = [(segment, 0.0)]
            lead = clause_prefix(segment)
            if lead:
                # A whole sentence is preferred over its leading clause.
                options.append((lead, -2.0))
            for raw, penalty in options:
                quote = display_form(raw, profile)
                if quote is None:
                    continue
                title = build_title(quote, asker, answerer)
                if title_failures(title, row, profile):
                    continue
                found.append(
                    {
                        "quote": quote,
                        "speaker": label,
                        "role": role,
                        "title": title,
                        "score": _candidate_score(quote, role, order) + penalty,
                    }
                )
    found.sort(key=lambda item: -item["score"])
    return found


def description_quote(
    row: dict[str, Any],
    *,
    avoid: str,
    prefer_speaker: str,
    profile: Profile = STRICT,
) -> dict[str, Any] | None:
    """Best verbatim clause for the description. Prefers the seat that did not speak the title."""
    asker, answerer = seat_names(row)
    best: tuple[float, dict[str, Any]] | None = None
    for order, turn in enumerate(_read_turns(row)):
        label = _turn_label(turn)
        role = str(turn.get("role") or "")
        if label not in {asker, answerer} or role not in {"orchestrator", "target"}:
            continue
        for raw in segments(str(turn.get("text") or "")):
            quote = display_form(raw, profile)
            if (
                quote is None
                or quote.casefold() == avoid.casefold()
                or len(quote) > 110
                or quote_failures(quote, profile)
            ):
                continue
            score = _candidate_score(quote, role, order) + (3 if label == prefer_speaker else 0)
            if best is None or score > best[0]:
                best = (score, {"quote": quote, "speaker": label})
    return best[1] if best else None


def build_description(quote: str, speaker: str, asker: str, answerer: str) -> str:
    tags = ["#shorts", "#AIvsAI", MODEL_HASHTAG[asker], MODEL_HASHTAG[answerer]]
    return (
        f'"{quote}" \u2014 {speaker}\n\n'
        f"{TEAM_LINE.format(a=asker, b=answerer)}\n\n"
        f"{' '.join(tags)}"
    )


def _parse_when(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _iso_utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _brt_text(value: str) -> str:
    moment = _parse_when(value)
    return moment.astimezone(BRT).strftime("%Y-%m-%d %H:%M") if moment else ""


def slot_iso(day: date) -> str:
    return _iso_utc(datetime.combine(day, SLOT, tzinfo=BRT))


def plan_slots(scheduled: list[dict[str, Any]]) -> dict[str, str]:
    """New ``publishAt`` per video id: one per day at 19:30 BRT.

    On each day the video already at 19:30 stays. If none is at 19:30, the
    earliest video of that day is retimed to 19:30. Every other video moves to
    the earliest free day, in original publish order.
    """
    by_day: dict[date, list[dict[str, Any]]] = {}
    for video in sorted(scheduled, key=lambda item: _parse_when(item["publish_at"])):
        local = _parse_when(video["publish_at"]).astimezone(BRT)
        by_day.setdefault(local.date(), []).append(video)
    result: dict[str, str] = {}
    movers: list[dict[str, Any]] = []
    for day, videos in sorted(by_day.items()):
        at_slot = [
            video
            for video in videos
            if _parse_when(video["publish_at"]).astimezone(BRT).time().replace(second=0) == SLOT
        ]
        keeper = at_slot[0] if at_slot else videos[0]
        result[keeper["id"]] = slot_iso(day)
        movers.extend(video for video in videos if video is not keeper)
    movers.sort(key=lambda item: _parse_when(item["publish_at"]))
    taken = {_parse_when(value).astimezone(BRT).date() for value in result.values()}
    cursor = min(by_day) if by_day else date.today()
    for video in movers:
        while cursor in taken:
            cursor += timedelta(days=1)
        result[video["id"]] = slot_iso(cursor)
        taken.add(cursor)
    return result


def schedule_failures(new_times: dict[str, str], *, now: datetime) -> list[str]:
    failures: list[str] = []
    days = Counter()
    for vid, value in new_times.items():
        moment = _parse_when(value)
        local = moment.astimezone(BRT)
        days[local.date()] += 1
        if (local.hour, local.minute) != (SLOT.hour, SLOT.minute):
            failures.append(f"{vid}: not 19:30 BRT ({local:%H:%M})")
        if local.hour == 12:
            failures.append(f"{vid}: still at 12:00")
        if moment <= now:
            failures.append(f"{vid}: publishAt is in the past")
    failures.extend(f"{day}: {count} videos on one day" for day, count in days.items() if count > 1)
    return failures


def normalize_live(video: dict[str, Any]) -> dict[str, Any]:
    status = video.get("status") if isinstance(video.get("status"), dict) else {}
    snippet = video.get("snippet") if isinstance(video.get("snippet"), dict) else {}
    return {
        "id": str(video.get("id") or ""),
        "privacy": str(status.get("privacyStatus") or "").lower(),
        "publish_at": str(status.get("publishAt") or ""),
        "title": str(snippet.get("title") or ""),
        "description": str(snippet.get("description") or ""),
        "snippet": snippet,
        "status": status,
    }


def select_targets(live: dict[str, dict[str, Any]], *, now: datetime) -> list[dict[str, Any]]:
    """Private scheduled videos (future ``publishAt``) plus the extra video ids."""
    chosen: dict[str, dict[str, Any]] = {}
    for vid, video in live.items():
        when = _parse_when(video["publish_at"])
        if video["privacy"] == "private" and when is not None and when > now:
            chosen[vid] = video
    for vid in EXTRA_VIDEO_IDS + RETITLE_PUBLIC_IDS:
        if vid in live:
            chosen.setdefault(vid, live[vid])
    return sorted(
        chosen.values(),
        key=lambda item: (_parse_when(item["publish_at"]) or datetime.max.replace(tzinfo=timezone.utc), item["id"]),
    )


def load_overrides(path: Path = OVERRIDES_PATH) -> dict[str, Any]:
    """Hand-written picks: ``manual_titles``, ``quote_overrides`` and ``paraphrases``."""
    data: dict[str, Any] = {}
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
    for key in ("manual_titles", "quote_overrides", "paraphrases"):
        data.setdefault(key, {})
    return data


def _holder_label(row: dict[str, Any], line: str, allowed: set[str]) -> str:
    """Seat that spoke ``line`` as a whole clause, or '' when nobody in ``allowed`` did."""
    turns = _read_turns(row)
    for turn in find_quote(turns, line) + find_clause(turns, line):
        if _turn_label(turn) in allowed:
            return _turn_label(turn)
    return ""


def _pick_quote_title(row: dict[str, Any], asker: str, answerer: str, line: str) -> dict[str, Any] | None:
    shown = display_form(line, RELAXED)
    speaker = _holder_label(row, line, {asker, answerer})
    if shown is None or not speaker:
        return None
    return {
        "title": build_title(shown, asker, answerer),
        "quote": shown,
        "speaker": speaker,
        "source": "override",
        "kind": quote_kind(row, shown),
    }


def _pick_paraphrase(
    row: dict[str, Any], asker: str, answerer: str, entry: dict[str, Any]
) -> dict[str, Any]:
    line = str(entry.get("source_line") or "")
    shown = display_form(line, RELAXED) if line else None
    speaker = _holder_label(row, line, {asker, answerer}) if line else ""
    return {
        "title": f"{str(entry.get('title') or '').strip()} | {asker} vs {answerer}",
        "quote": "",
        "speaker": speaker,
        "source": "paraphrase",
        "kind": "paraphrase",
        "source_line": shown or "",
    }


def _unique_candidate(
    candidates: list[dict[str, Any]], used_titles: set[str], used_quotes: set[str]
) -> dict[str, Any] | None:
    return next(
        (
            item
            for item in candidates
            if item["title"].casefold() not in used_titles
            and item["quote"].casefold() not in used_quotes
        ),
        None,
    )


def _description_for(
    row: dict[str, Any], picked: dict[str, Any], asker: str, answerer: str
) -> dict[str, Any] | None:
    """Verbatim description quote: the paraphrase source line, else the best other line."""
    if picked["kind"] == "paraphrase" and picked.get("source_line") and picked["speaker"]:
        return {"quote": picked["source_line"], "speaker": picked["speaker"]}
    other = answerer if picked["speaker"] == asker else asker
    avoid = picked["quote"] or picked.get("source_line", "")
    for profile in (STRICT, RELAXED):
        found = description_quote(row, avoid=avoid, prefer_speaker=other, profile=profile)
        if found:
            return found
    return None


def manual_failures(title: str, row: dict[str, Any]) -> list[str]:
    """Rules for a title the owner wrote by hand: quoted, but not necessarily spoken verbatim.

    Everything except the verbatim and second-person checks still applies:
    length, wrapper format, seat/model match, banned patterns, repeated words.
    """
    failures: list[str] = []
    if len(title) > TITLE_MAX:
        failures.append(f"over_{TITLE_MAX}_chars:{len(title)}")
    match = _TITLE_RE.match(title)
    if not match:
        return failures + ["format"]
    quote, left, right = match["quote"], match["a"], match["b"]
    failures.extend(_model_failures(left, right, row))
    for problem in quote_failures(quote, RELAXED):
        # A finished question ("... where the shelf was?") is not a cut-off title.
        if problem == "dangling_end" and quote.endswith(("?", "!")):
            continue
        failures.append(problem)
    for pattern in BANNED_TITLE_PATTERNS:
        if pattern.search(title):
            failures.append(f"banned_pattern:{pattern.pattern}")
    repeats = repeated_words(title, RELAXED)
    if repeats:
        failures.append("repeated_word:" + ",".join(repeats))
    return failures


def title_problems(item: dict[str, Any], row: dict[str, Any]) -> list[str]:
    """Run the right validator for the title kind, plus the description validator."""
    if item.get("title_kind") == "paraphrase":
        failures = paraphrase_failures(item["new_title"], row)
    elif item.get("title_kind") == "manual":
        failures = manual_failures(item["new_title"], row)
    else:
        failures = title_failures(item["new_title"], row, RELAXED)
    if item["new_description"]:
        failures.extend(description_failures(item["new_description"], row))
    return failures


def build_plan(
    targets: list[dict[str, Any]],
    rows_by_video: dict[str, dict[str, Any]],
    other_live_titles: dict[str, str],
    *,
    now: datetime,
    overrides: dict[str, Any] | None = None,
    blocked_ids: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    overrides = {"quote_overrides": {}, "paraphrases": {}, "manual_titles": {}, **(overrides or {})}
    targets = [video for video in targets if video["id"] not in blocked_ids]
    scheduled = [item for item in targets if item["privacy"] == "private"]
    new_times = plan_slots(scheduled)
    used_titles = {title.casefold() for title in other_live_titles.values() if title}
    used_quotes: set[str] = set()
    items: list[dict[str, Any]] = []
    for video in targets:
        vid = video["id"]
        row = rows_by_video.get(vid)
        item: dict[str, Any] = {
            "video_id": vid,
            "session_id": str((row or {}).get("session_id") or ""),
            "privacy": video["privacy"],
            "old_title": video["title"],
            "old_description": video["description"],
            "old_publish_at": video["publish_at"],
            "old_publish_brt": _brt_text(video["publish_at"]),
            "new_title": "",
            "new_description": "",
            "title_quote": "",
            "new_publish_at": new_times.get(vid, ""),
            "new_publish_brt": _brt_text(new_times.get(vid, "")),
            "failures": [],
        }
        item["slot_changed"] = bool(item["new_publish_at"]) and item["new_publish_at"] != _iso_utc(
            _parse_when(video["publish_at"])
        )
        if row is None:
            item["failures"].append("no_library_row_with_transcript")
            items.append(item)
            continue
        asker, answerer = seat_names(row)
        picked: dict[str, Any] | None = None
        if vid in overrides["manual_titles"]:
            # Owner-written title: used exactly as given, after the manual rules.
            text = str(overrides["manual_titles"][vid]).strip()
            picked = {
                "title": build_title(text, asker, answerer),
                "quote": text,
                "speaker": "",
                "source": "manual",
                "kind": "manual",
            }
        elif vid in overrides["quote_overrides"]:
            picked = _pick_quote_title(row, asker, answerer, str(overrides["quote_overrides"][vid]))
            if picked is None:
                item["failures"].append("override_quote_not_spoken_by_these_seats")
        elif vid in overrides["paraphrases"]:
            # A hand-written paraphrase wins over the automatic picks.
            picked = _pick_paraphrase(row, asker, answerer, overrides["paraphrases"][vid])
            if not picked["speaker"]:
                item["failures"].append("paraphrase_source_line_not_verbatim_for_these_seats")
        else:
            for source, profile in (("strict", STRICT), ("relaxed", RELAXED)):
                picked = _unique_candidate(title_candidates(row, profile), used_titles, used_quotes)
                if picked is not None:
                    picked = {**picked, "source": source, "kind": quote_kind(row, picked["quote"])}
                    break
            if picked is None:
                item["failures"].append("no_title_candidate_fits_rules")
        if picked is None:
            items.append(item)
            continue
        used_titles.add(picked["title"].casefold())
        if picked["quote"]:
            used_quotes.add(picked["quote"].casefold())
        extra = _description_for(row, picked, asker, answerer)
        chosen = picked
        item["title_kind"] = chosen["source"]
        item["title_quote"] = chosen["quote"]
        item["title_quote_kind"] = chosen["kind"]
        item["title_speaker"] = chosen["speaker"]
        item["new_title"] = chosen["title"]
        item["models"] = f"{asker} vs {answerer}"
        old_models = re.search(r"\| ([A-Za-z0-9-]+) vs ([A-Za-z0-9-]+)\s*$", video["title"])
        item["old_title_models"] = f"{old_models[1]} vs {old_models[2]}" if old_models else ""
        item["models_changed"] = bool(old_models) and item["old_title_models"] != item["models"]
        if extra is None:
            item["failures"].append("no_description_quote")
        else:
            item["description_quote"] = extra["quote"]
            item["description_speaker"] = extra["speaker"]
            item["new_description"] = build_description(extra["quote"], extra["speaker"], asker, answerer)
        item["failures"].extend(title_problems(item, row))
        items.append(item)
    for vid, problems in plan_failures(items, other_live_titles).items():
        next(item for item in items if item["video_id"] == vid)["failures"].extend(problems)
    for problem in schedule_failures(new_times, now=now):
        owner = next((item for item in items if problem.startswith(item["video_id"] + ":")), None)
        if owner is not None:
            owner["failures"].append(f"schedule:{problem}")
        else:
            # Day-level problem such as two videos on one day: fail every item.
            for item in items:
                item["failures"].append(f"schedule:{problem}")
    for item in items:
        item["valid"] = not item["failures"]
    return items


CSV_COLUMNS = (
    "video_id",
    "session_id",
    "privacy",
    "old_title",
    "new_title",
    "new_title_chars",
    "title_kind",
    "quote_kind",
    "old_description",
    "new_description",
    "old_publish_brt",
    "new_publish_brt",
    "slot_changed",
    "valid",
    "failures",
    "applied",
    "note",
)


def write_csv(
    items: list[dict[str, Any]],
    path: Path,
    blocked: list[dict[str, Any]] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_COLUMNS)
        for item in items:
            writer.writerow(
                [
                    item["video_id"],
                    item["session_id"],
                    item["privacy"],
                    item["old_title"],
                    item["new_title"],
                    len(item["new_title"]),
                    item.get("title_kind", ""),
                    item.get("title_quote_kind", ""),
                    item["old_description"].replace("\r", "").replace("\n", "\\n"),
                    item["new_description"].replace("\n", "\\n"),
                    item["old_publish_brt"],
                    item["new_publish_brt"],
                    "yes" if item["slot_changed"] else "no",
                    "yes" if item.get("valid") else "no",
                    "; ".join(item["failures"]),
                    item.get("applied", ""),
                    "",
                ]
            )
        for entry in blocked or []:
            writer.writerow(
                [
                    entry["video_id"],
                    entry["session_id"],
                    entry["privacy"],
                    entry["old_title"],
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    entry["old_publish_brt"],
                    "(schedule removed)" if entry["action"] == "unschedule" else entry["old_publish_brt"],
                    "no",
                    "excluded",
                    "",
                    entry.get("applied", ""),
                    entry["note"],
                ]
            )


def _plan_sha(items: list[dict[str, Any]], unschedule: list[dict[str, Any]]) -> str:
    body = {"items": items, "unschedule": unschedule}
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def blocked_entries(
    rows: dict[str, dict[str, Any]],
    library_rows: list[dict[str, Any]],
    live: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], frozenset[str]]:
    """Blocklisted sessions: their video ids, live state, and what ``apply`` will do."""
    by_session = {row["session_id"]: vid for vid, row in rows.items()}
    entries: list[dict[str, Any]] = []
    video_ids: set[str] = set()
    for session_id, reason in BLOCKED_SESSIONS.items():
        vid = by_session.get(session_id, "")
        state = live.get(vid) if vid else None
        unschedule = bool(
            state
            and session_id in UNSCHEDULE_SESSIONS
            and state["privacy"] == "private"
            and state["publish_at"]
        )
        if vid:
            video_ids.add(vid)
        in_library = any(str(row.get("session_id")) == session_id for row in library_rows)
        entries.append(
            {
                "video_id": vid,
                "session_id": session_id,
                "privacy": state["privacy"] if state else "",
                "old_title": state["title"] if state else "",
                "old_publish_at": state["publish_at"] if state else "",
                "old_publish_brt": _brt_text(state["publish_at"]) if state else "",
                "action": "unschedule" if unschedule else "exclude",
                "note": (
                    f"blocklisted ({reason}). "
                    + (
                        "apply removes publishAt and keeps it private."
                        if unschedule
                        else "never uploaded."
                        if not vid
                        else "left as it is on YouTube."
                    )
                    + ("" if in_library else " not in library.")
                ),
            }
        )
    return entries, frozenset(video_ids)


def _youtube():
    from distribution.publishers.api.youtube_upload import build_youtube_client_for_page

    return build_youtube_client_for_page(CHANNEL_ID, enforce_channel=True)


def fetch_live(youtube: Any, ids: list[str]) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for start in range(0, len(ids), 50):
        response = (
            youtube.videos().list(part="snippet,status", id=",".join(ids[start : start + 50])).execute()
        )
        for raw in response.get("items") or []:
            video = normalize_live(raw)
            found[video["id"]] = video
    return found


def library_rows_by_video() -> dict[str, dict[str, Any]]:
    from shared.contracts import load_distribution_library

    rows = load_distribution_library(LIBRARY)
    found: dict[str, dict[str, Any]] = {}
    for row in rows:
        youtube = (row.get("platform_overrides") or {}).get("youtube") or {}
        vid = str(youtube.get("video_id") or row.get("youtube_video_id") or "").strip()
        if vid:
            found[vid] = row
    return found


def cmd_plan(args: argparse.Namespace) -> int:
    now = datetime.now(timezone.utc)
    rows = library_rows_by_video()
    youtube = _youtube()
    live = fetch_live(youtube, sorted(rows))
    targets = select_targets(live, now=now)
    from distribution.publishers.api.youtube_upload import list_channel_upload_videos

    uploads = list_channel_upload_videos(youtube, max_pages=20)
    target_ids = {item["id"] for item in targets}
    other_titles = {
        str(upload.get("video_id") or ""): str(upload.get("title") or "")
        for upload in uploads
        if str(upload.get("video_id") or "") not in target_ids
    }
    from shared.contracts import load_distribution_library

    blocked, blocked_ids = blocked_entries(rows, load_distribution_library(LIBRARY), live)
    items = build_plan(
        targets,
        rows,
        other_titles,
        now=now,
        overrides=load_overrides(),
        blocked_ids=blocked_ids,
    )
    unschedule = [entry for entry in blocked if entry["action"] == "unschedule"]
    out_dir = review_path(CHANNEL_ID, "retitle_v4")
    out_dir.mkdir(parents=True, exist_ok=True)
    plan_path = out_dir / "plan.json"
    payload = {
        "generated_at": _iso_utc(now),
        "plan_sha256": _plan_sha(items, unschedule),
        "slot": "19:30 America/Sao_Paulo, one video per day",
        "items": items,
        "blocked": blocked,
        "unschedule": unschedule,
    }
    plan_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_csv(items, DOCS_DIR / "retitle_v4_before_after.csv", blocked)
    valid = sum(1 for item in items if item["valid"])
    print(f"targets={len(items)} valid={valid} invalid={len(items) - valid}")
    print(f"plan -> {plan_path}")
    print(f"csv  -> {DOCS_DIR / 'retitle_v4_before_after.csv'}")
    for item in items:
        if not item["valid"]:
            print(f"INVALID {item['video_id']} {item['failures']}")
    return 0


def candidate_report(row: dict[str, Any], *, limit: int = 3) -> list[dict[str, Any]]:
    """Best spoken lines for an episode with no valid title, and what blocks each one.

    Second-person clauses come first. When an episode has none, its strongest
    lines are listed and marked ``no_second_person``.
    """
    asker, answerer = seat_names(row)
    found: list[dict[str, Any]] = []
    for order, turn in enumerate(_read_turns(row)):
        label = _turn_label(turn)
        role = str(turn.get("role") or "")
        if label not in {asker, answerer} or role not in {"orchestrator", "target"}:
            continue
        for segment in segments(str(turn.get("text") or "")):
            title = build_title(segment, asker, answerer)
            found.append(
                {
                    "quote": segment,
                    "speaker": label,
                    "chars": len(title),
                    "blockers": [text.split(":")[0] for text in title_failures(title, row)],
                    "second_person": bool(SECOND_PERSON.search(_straight(segment))),
                    "score": _candidate_score(segment, role, order),
                }
            )
    found.sort(key=lambda item: (not item["second_person"], len(item["blockers"]), -item["score"]))
    return found[:limit]


def cmd_unresolved(args: argparse.Namespace) -> int:
    plan_path = review_path(CHANNEL_ID, "retitle_v4", "plan.json")
    items = json.loads(plan_path.read_text(encoding="utf-8"))["items"]
    rows = library_rows_by_video()
    out = DOCS_DIR / "retitle_v4_unresolved.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with out.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["video_id", "models", "old_title", "rank", "line", "speaker", "title_chars", "blocked_by"]
        )
        for item in items:
            if item["valid"]:
                continue
            count += 1
            row = rows[item["video_id"]]
            asker, answerer = seat_names(row)
            for rank, cand in enumerate(candidate_report(row), start=1):
                blockers = list(cand["blockers"]) + ([] if cand["second_person"] else ["no_second_person"])
                writer.writerow(
                    [
                        item["video_id"],
                        f"{asker} vs {answerer}",
                        item["old_title"],
                        rank,
                        cand["quote"],
                        cand["speaker"],
                        cand["chars"],
                        "; ".join(blockers),
                    ]
                )
    print(f"unresolved={count} -> {out}")
    return 0


def _snippet_body(live: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    current = live["snippet"]
    body: dict[str, Any] = {
        "title": item["new_title"],
        "description": item["new_description"],
        "categoryId": str(current.get("categoryId") or "28"),
    }
    for key in ("tags", "defaultLanguage", "defaultAudioLanguage"):
        if current.get(key):
            body[key] = current[key]
    return body


def _status_body(live: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    status = live["status"]
    body: dict[str, Any] = {"privacyStatus": "private", "publishAt": item["new_publish_at"]}
    for key in ("license", "embeddable", "publicStatsViewable", "selfDeclaredMadeForKids"):
        if key in status:
            body[key] = status[key]
    return body


def _unschedule_status_body(live: dict[str, Any]) -> dict[str, Any]:
    """Status that stays private and carries no ``publishAt``."""
    status = live["status"]
    body: dict[str, Any] = {"privacyStatus": "private"}
    for key in ("license", "embeddable", "publicStatsViewable", "selfDeclaredMadeForKids"):
        if key in status:
            body[key] = status[key]
    return body


def apply_unschedule(youtube: Any, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove ``publishAt`` from blocklisted videos. They stay private. Verified by readback."""
    results: list[dict[str, Any]] = []
    live = fetch_live(youtube, [entry["video_id"] for entry in entries]) if entries else {}
    for entry in entries:
        vid = entry["video_id"]
        current = live.get(vid)
        result: dict[str, Any] = {"video_id": vid, "action": "unschedule", "ok": False}
        results.append(result)
        if current is None:
            result["error"] = "missing_from_videos_list"
            continue
        if current["privacy"] != "private" or current["publish_at"] != entry["old_publish_at"]:
            result["error"] = "live_state_changed_since_plan"
            continue
        try:
            youtube.videos().update(
                part="status", body={"id": vid, "status": _unschedule_status_body(current)}
            ).execute()
        except Exception as exc:  # noqa: BLE001
            result["error"] = f"update_failed:{type(exc).__name__}:{exc}"
            continue
        # YouTube can serve the old status for a few seconds after an update.
        for attempt in range(6):
            later = fetch_live(youtube, [vid]).get(vid)
            result["readback"] = {
                "still_private": later is not None and later["privacy"] == "private",
                "publish_at_cleared": later is not None and not later["publish_at"],
            }
            result["readback_attempts"] = attempt + 1
            if all(result["readback"].values()):
                break
            time.sleep(3)
        result["ok"] = all(result["readback"].values())
    return results


def cmd_apply(args: argparse.Namespace) -> int:
    payload = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    items = payload["items"]
    unschedule = payload.get("unschedule", [])
    if _plan_sha(items, unschedule) != payload["plan_sha256"]:
        raise SystemExit("refusing --apply: plan file was edited after it was generated")
    invalid = [item["video_id"] for item in items if not item.get("valid")]
    if invalid:
        raise SystemExit(f"refusing --apply: {len(invalid)} invalid items {invalid}")
    youtube = _youtube()
    live = fetch_live(youtube, [item["video_id"] for item in items])
    log: dict[str, Any] = {
        "started_at": _iso_utc(datetime.now(timezone.utc)),
        "plan_sha256": payload["plan_sha256"],
        "quota_units_estimate": len(items) * QUOTA_UNITS_PER_UPDATE,
        "results": [],
    }
    for item in items:
        vid = item["video_id"]
        current = live.get(vid)
        result: dict[str, Any] = {
            "video_id": vid,
            "ok": False,
            "old_title": item["old_title"],
            "new_title": item["new_title"],
        }
        log["results"].append(result)
        if current is None:
            result["error"] = "missing_from_videos_list"
            continue
        drift = [
            name
            for name, now_value, was in (
                ("title", current["title"], item["old_title"]),
                ("description", current["description"], item["old_description"]),
                ("privacy", current["privacy"], item["privacy"]),
                ("publish_at", current["publish_at"], item["old_publish_at"]),
            )
            if now_value != was
        ]
        if drift:
            result["error"] = "live_state_changed_since_plan:" + ",".join(drift)
            continue
        parts = ["snippet"]
        body: dict[str, Any] = {"id": vid, "snippet": _snippet_body(current, item)}
        if item["slot_changed"] and current["privacy"] == "private":
            parts.append("status")
            body["status"] = _status_body(current, item)
        try:
            youtube.videos().update(part=",".join(parts), body=body).execute()
        except Exception as exc:  # noqa: BLE001
            result["error"] = f"update_failed:{type(exc).__name__}:{exc}"
            _LOG.error("update failed %s: %s", vid, exc)
            continue
        result["parts"] = parts
        result["ok"] = True
        _LOG.info("updated %s parts=%s title=%s", vid, parts, item["new_title"])

    after = fetch_live(youtube, [item["video_id"] for item in items])
    for item, result in zip(items, log["results"]):
        if not result["ok"]:
            item["applied"] = "no"
            continue
        later = after.get(item["video_id"])
        wanted_at = item["new_publish_at"] if item["slot_changed"] else item["old_publish_at"]
        checks = {
            "title": later is not None and later["title"] == item["new_title"],
            "description": later is not None and later["description"].strip() == item["new_description"].strip(),
            "privacy_unchanged": later is not None and later["privacy"] == item["privacy"],
            "publish_at": later is not None
            and (item["privacy"] != "private" or _parse_when(later["publish_at"]) == _parse_when(wanted_at)),
        }
        result["readback"] = checks
        result["ok"] = all(checks.values())
        item["applied"] = "yes" if result["ok"] else "no"
    log["unschedule"] = apply_unschedule(youtube, unschedule)
    for entry, result in zip(unschedule, log["unschedule"]):
        entry["applied"] = "yes" if result["ok"] else "no"
    log["finished_at"] = _iso_utc(datetime.now(timezone.utc))
    log["ok"] = sum(1 for result in log["results"] if result["ok"])
    log["failed"] = (len(items) - log["ok"]) + sum(1 for r in log["unschedule"] if not r["ok"])
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    (DOCS_DIR / "retitle_v4_apply_log.json").write_text(
        json.dumps(log, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    blocked = payload.get("blocked", [])
    applied_by_video = {entry["video_id"]: entry.get("applied", "") for entry in unschedule}
    for entry in blocked:
        entry["applied"] = applied_by_video.get(entry["video_id"], entry.get("applied", ""))
    write_csv(items, DOCS_DIR / "retitle_v4_before_after.csv", blocked)
    print(f"applied ok={log['ok']} failed={log['failed']}")
    return 1 if log["failed"] else 0


def verification(
    items: list[dict[str, Any]],
    unschedule: list[dict[str, Any]],
    live: dict[str, dict[str, Any]],
    *,
    now: datetime,
) -> dict[str, Any]:
    """Compare live YouTube state with the plan. Pure function of its inputs."""
    problems: list[str] = []
    at_noon = 0
    for item in items:
        vid = item["video_id"]
        current = live.get(vid)
        if current is None:
            problems.append(f"{vid}: missing")
            continue
        if current["title"] != item["new_title"]:
            problems.append(f"{vid}: title differs")
        if current["description"].strip() != item["new_description"].strip():
            problems.append(f"{vid}: description differs")
        if current["privacy"] != item["privacy"]:
            problems.append(f"{vid}: privacy {item['privacy']} -> {current['privacy']}")
        if item["privacy"] == "private" and item["new_publish_at"]:
            if _parse_when(current["publish_at"]) != _parse_when(item["new_publish_at"]):
                problems.append(f"{vid}: publishAt differs")
            local = _parse_when(current["publish_at"]).astimezone(BRT)
            if local.hour == 12:
                at_noon += 1
    for entry in unschedule:
        current = live.get(entry["video_id"])
        if current is None or current["privacy"] != "private" or current["publish_at"]:
            problems.append(f"{entry['video_id']}: not private with an empty publishAt")
    return {
        "checked_at": _iso_utc(now),
        "videos_checked": len(items) + len(unschedule),
        "scheduled_at_12_00": at_noon,
        "problems": problems,
        "ok": not problems and at_noon == 0,
    }


def cmd_verify(args: argparse.Namespace) -> int:
    payload = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    items, unschedule = payload["items"], payload.get("unschedule", [])
    youtube = _youtube()
    ids = [item["video_id"] for item in items] + [entry["video_id"] for entry in unschedule]
    live = fetch_live(youtube, ids)
    report = verification(items, unschedule, live, now=datetime.now(timezone.utc))
    # Whole-library view: no private video may still sit at 12:00 BRT.
    rows = library_rows_by_video()
    everything = fetch_live(youtube, sorted(rows))
    noon = [
        vid
        for vid, video in everything.items()
        if video["privacy"] == "private"
        and video["publish_at"]
        and _parse_when(video["publish_at"]).astimezone(BRT).hour == 12
    ]
    report["private_scheduled_at_12_00_anywhere"] = noon
    report["ok"] = report["ok"] and not noon
    out = DOCS_DIR / "retitle_v4_verify.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        f"verified={report['videos_checked']} problems={len(report['problems'])} "
        f"at_12_00={len(noon)} ok={report['ok']}"
    )
    for problem in report["problems"]:
        print("PROBLEM", problem)
    return 0 if report["ok"] else 1


def sync_rows(
    rows: list[dict[str, Any]],
    items: list[dict[str, Any]],
    ok_ids: set[str],
    unscheduled_ids: set[str],
) -> int:
    """Copy what YouTube now has into the library so later tools do not revert it.

    Only videos whose update was verified are touched. Returns the number of rows changed.
    """
    by_video = {
        str(((row.get("platform_overrides") or {}).get("youtube") or {}).get("video_id") or ""): row
        for row in rows
    }
    changed = 0
    for item in items:
        row = by_video.get(item["video_id"])
        if row is None or item["video_id"] not in ok_ids:
            continue
        youtube = row["platform_overrides"]["youtube"]
        youtube["title"] = item["new_title"]
        youtube["caption"] = item["new_description"]
        if item["new_publish_at"]:
            youtube["scheduled_time"] = item["new_publish_at"]
        if isinstance(row.get("base_metadata"), dict):
            row["base_metadata"]["title"] = item["new_title"]
        changed += 1
    for vid in unscheduled_ids:
        row = by_video.get(vid)
        if row is not None:
            row["platform_overrides"]["youtube"]["scheduled_time"] = ""
            changed += 1
    return changed


def cmd_sync_library(args: argparse.Namespace) -> int:
    import shutil

    from shared.contracts import load_distribution_library, save_distribution_library

    payload = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    log = json.loads((DOCS_DIR / "retitle_v4_apply_log.json").read_text(encoding="utf-8"))
    ok_ids = {result["video_id"] for result in log["results"] if result["ok"]}
    unscheduled = {result["video_id"] for result in log.get("unschedule", []) if result["ok"]}
    rows = load_distribution_library(LIBRARY)
    backup = temp_path(CHANNEL_ID, "content_library.pre_retitle_v4.json")
    shutil.copyfile(LIBRARY, backup)
    changed = sync_rows(rows, payload["items"], ok_ids, unscheduled)
    save_distribution_library(LIBRARY, rows)
    # Read back from disk and compare.
    saved = {
        str(((row.get("platform_overrides") or {}).get("youtube") or {}).get("video_id") or ""): row
        for row in load_distribution_library(LIBRARY)
    }
    wrong = [
        item["video_id"]
        for item in payload["items"]
        if item["video_id"] in ok_ids
        and (saved[item["video_id"]]["platform_overrides"]["youtube"]["title"] != item["new_title"])
    ]
    print(f"library rows changed={changed} mismatches={len(wrong)} backup={backup}")
    return 1 if wrong else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aiwake-retitle-v4")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("plan", help="Build the plan, validate it, and write the before/after CSV.")
    sub.add_parser("unresolved", help="List the best lines for videos with no valid title.")
    apply = sub.add_parser("apply", help="Update titles, descriptions, and slots via the YouTube API.")
    apply.add_argument("--plan", type=Path, required=True)
    verify = sub.add_parser("verify", help="Read every video back and compare it with the plan.")
    verify.add_argument("--plan", type=Path, required=True)
    sync = sub.add_parser("sync-library", help="Copy verified titles, descriptions, and slots into the library.")
    sync.add_argument("--plan", type=Path, required=True)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.command == "plan":
        return cmd_plan(args)
    if args.command == "unresolved":
        return cmd_unresolved(args)
    if args.command == "verify":
        return cmd_verify(args)
    if args.command == "sync-library":
        return cmd_sync_library(args)
    return cmd_apply(args)


if __name__ == "__main__":
    raise SystemExit(main())
