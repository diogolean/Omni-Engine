# -*- coding: utf-8 -*-
"""Hard checks for Aiwake captions v3.

    python -m channels_config.aiwake.tools.validate_aiwake_captions [library.json]
    python tools/validate_aiwake_captions.py [library.json] [--ids scope.json]

Ready rows are checked. ``needs_review`` is reported and is not a failure.
Non-ready rows must have empty live captions and a ``legacy_captions`` object.
"""
from __future__ import annotations

import argparse
import collections
import difflib
import json
import re
import sys
from pathlib import Path
from typing import Any

from channels_config.aiwake.tools.caption_generator import (
    BANNED_PHRASES,
    BROAD_TAGS,
    _DANGLING,
    _clean,
    _read_turns,
    allowed_hashtags,
    build_headline,
    display_name,
    seat_names,
)
from channels_config.aiwake.tools.metadata_generator import is_portuguese
from channels_config.aiwake.tools.production_status import (
    PRODUCTION_STATUS_VALUES,
    posting_order,
)

VALIDATOR_VERSION = "captions_v4"

_HEADLINE_RE = re.compile(
    r"^(Gemini|Llama|GPT-4o|DeepSeek|Claude) vs "
    r"(Gemini|Llama|GPT-4o|DeepSeek|Claude) - .+[?.!]$"
)
_MODEL_RE = re.compile(
    r"\b(GPT-4o|GPT-[\w.]+|Gemini(?: [\d.]+)?(?: Flash| Pro)?|"
    r"Claude(?: Sonnet| Opus| Haiku)?(?: [\d.]+)?|"
    r"Llama(?: [\d.]+)?(?: \d+B)?|DeepSeek(?: Chat| R\d)?|"
    r"Grok(?: [\d.]+)?|Mistral\w*|Qwen[\w.]*)\b"
)
_PLACEHOLDER = re.compile(
    r"AIWAKE\.CORE|TARGET\.NODE|\[Unscripted AI Battle\]|\{\w+\}|<M>|TODO|lorem",
    re.IGNORECASE,
)
_VERDICT = re.compile(
    r"\b(\w*admit\w*|\w*corner\w*|\w*confess\w*|\w*collaps\w*|\w*dodg\w*|caught)\b",
    re.IGNORECASE,
)
_AI_CUE = re.compile(r"\bAI\b|AI-generated|AI-animated|AI voices", re.IGNORECASE)
_PT_EXTRA = re.compile(
    r"\b(?:não|nao|você|voce|está|estão|também|tambem|porque|obrigado|"
    r"falou|mandou|então|entao|agora|gente|coisa|muito)\b",
    re.IGNORECASE,
)
_PT_CHAR = re.compile(r"[ãõçáàâéêíóôú]")
_EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002700-\U000027BF\U0001F1E6-\U0001F1FF]"
)
_GENERIC_CLOSERS = {
    "who won this round?",
    "which side are you taking?",
    "does the excuse hold?",
    "does that concession hold?",
}
_PLATFORMS = {
    "tiktok": ("platform_overrides.tiktok.caption", 300, 80, 3, 3),
    "instagram": ("platform_overrides.instagram.caption", 2200, 125, 3, 3),
    "facebook": ("platform_overrides.facebook.caption", 1000, 125, 3, 3),
    "youtube": ("platform_overrides.youtube.caption", 700, 100, 3, 3),
    "kwai": ("platform_overrides.kwai.caption", 2200, 80, 3, 3),
    "x": ("platform_overrides.x.caption", 280, 280, 0, 2),
    "linkedin": ("linkedin_caption", 3000, 150, 0, 3),
}
_FEED = ("tiktok", "instagram", "facebook", "youtube", "kwai")
_LIVE_TOP = (
    "final_caption",
    "humanized_caption",
    "post_planner_caption",
    "tiktok_caption",
    "facebook_caption",
    "linkedin_caption",
)
_CAUGHT = re.compile(
    r"\b(?:Gemini|Llama|GPT-4o|DeepSeek|Claude)\s+caught\s+"
    r"(?:Gemini|Llama|GPT-4o|DeepSeek|Claude)\b",
    re.IGNORECASE,
)


def _get(row: dict[str, Any], path: str) -> Any:
    current: Any = row
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _qa(row: dict[str, Any]) -> dict[str, Any]:
    qa = row.get("caption_qa")
    return qa if isinstance(qa, dict) else {}


def _ready_ok(row: dict[str, Any]) -> bool:
    if str(row.get("production_status") or "") != "ready":
        return False
    if str(row.get("production_scope") or "") == "out":
        return False
    review = row.get("quality_review") if isinstance(row.get("quality_review"), dict) else {}
    if str(review.get("status") or "") == "fail":
        return False
    qa = _qa(row)
    return (
        str(qa.get("status") or "") == "ok"
        and str(qa.get("generator") or "") == "captions_v4"
        and bool(str(qa.get("model") or "").strip())
    )


def _lines(text: str) -> list[str]:
    return [
        line.strip()
        for line in str(text or "").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _tags(text: str) -> list[str]:
    return re.findall(r"#\w+", text or "")


def _norm_tokens(text: str) -> list[str]:
    cleaned = str(text or "").lower().replace("…", " ").replace("...", " ")
    cleaned = re.sub(r"[^\w\s']", " ", cleaned)
    return re.findall(r"[a-z0-9']+", cleaned)


def _dangling(line: str) -> bool:
    if not str(line).rstrip().endswith("?"):
        return False
    words = re.findall(r"[\w']+", line)
    if len(words) < 4:
        return False
    last = words[-1].lower()
    if last in _DANGLING:
        return True
    if last == "you" and words[-2].lower() in {"if", "whether"}:
        return True
    return line.rstrip("?").rstrip().endswith("...")


def _ws(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _quotes(text: str) -> list[str]:
    return [item.strip() for item in re.findall(r'"([^"]+)"', text or "") if item.strip()]


def _quote_in_utterance(quote: str, utterance: str) -> bool:
    spoken = _ws(utterance)
    raw = _ws(quote).strip('"')
    core = raw
    if core.endswith("…") or core.endswith("..."):
        core = core.rstrip(".").rstrip("…").strip()
    if not core:
        return False
    if core in spoken:
        return True
    if spoken.startswith(core) and (len(spoken) == len(core) or spoken[len(core):len(core) + 1] in {" ", ""}):
        return True
    return False


def _transcript_tokens(row: dict[str, Any]) -> set[str]:
    tokens: set[str] = set()
    for turn in _read_turns(row):
        tokens.update(_norm_tokens(str(turn.get("text") or "")))
    return tokens


def _mask(line: str, row: dict[str, Any]) -> str:
    text = re.sub(r"#\w+", " ", line or "")
    text = re.sub(r'"[^"]*"', " <Q> ", text)
    text = _MODEL_RE.sub("<M>", text)
    text = re.sub(r"\d+", "<N>", text)
    known = _transcript_tokens(row)

    def _swap(match: re.Match[str]) -> str:
        word = match.group(0)
        if word.startswith("<"):
            return word
        if word.lower() in known:
            return "<W>"
        return word

    text = re.sub(r"[A-Za-z']+|<[A-Za-z]+>", _swap, text)
    text = re.sub(r"[^a-z<>\s]", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def _placeholder(token: str) -> bool:
    core = re.sub(r"[^a-z<>]", "", token.lower())
    return core in {"<w>", "<m>", "<q>", "<n>"}


def _editorial(masked: str) -> bool:
    for token in masked.split():
        if not _placeholder(token) and re.search(r"[a-z]", token):
            return True
    return False


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [part.strip() for part in parts if part.strip()]


def _body_sentences(caption: str) -> list[str]:
    """Sentences after the headline, without the disclosure line or hashtags."""
    lines = _lines(caption)
    if len(lines) > 1:
        lines = lines[1:]
    kept: list[str] = []
    for line in lines:
        if "unscripted" in line.lower():
            continue
        kept.extend(_sentences(line))
    return kept


def _disclosure_lines(caption: str) -> list[str]:
    return [line for line in _lines(caption) if "unscripted" in line.lower()]


def _closing(caption: str) -> str:
    sentences = _body_sentences(caption)
    questions = [item for item in sentences if item.endswith("?")]
    if questions:
        return questions[-1]
    return sentences[-1] if sentences else ""


def _opening(caption: str) -> str:
    sentences = _body_sentences(caption)
    return sentences[0] if sentences else ""


def _channel_metadata_only(row: dict[str, Any]) -> bool:
    """Rejected uploads may keep the YouTube snippet that is already on the channel."""
    if _live_captions_empty(row):
        return True
    yt = ((row.get("platform_overrides") or {}).get("youtube") or {})
    if not str(yt.get("video_id") or "").strip():
        return False
    clone = json.loads(json.dumps(row))
    block = ((clone.get("platform_overrides") or {}).get("youtube") or {})
    block["title"] = ""
    block["caption"] = ""
    base = clone.get("base_metadata") if isinstance(clone.get("base_metadata"), dict) else {}
    if str(base.get("title") or "") == str(yt.get("title") or ""):
        base["title"] = ""
    if str(base.get("caption") or "") == str(yt.get("caption") or ""):
        base["caption"] = ""
    return _live_captions_empty(clone)


def _live_captions_empty(row: dict[str, Any]) -> bool:
    for key in _LIVE_TOP:
        if str(row.get(key) or "").strip():
            return False
    base = row.get("base_metadata") if isinstance(row.get("base_metadata"), dict) else {}
    if str(base.get("title") or "").strip() or str(base.get("caption") or "").strip():
        return False
    if list(base.get("hashtags") or []):
        return False
    overrides = row.get("platform_overrides") if isinstance(row.get("platform_overrides"), dict) else {}
    for block in overrides.values():
        if not isinstance(block, dict):
            continue
        for key in ("caption", "title", "description"):
            if str(block.get(key) or "").strip():
                return False
    return True


def _outside_quotes(text: str) -> str:
    return re.sub(r'"[^"]*"', " ", text or "")


def _run_on(topic: str, line: str) -> bool:
    if "..." not in line and "…" not in line:
        return False
    parts = re.split(r"\.{3}|…", line)
    if len(parts) < 2:
        return False
    left = _norm_tokens(parts[0])[-4:]
    right = _norm_tokens(parts[1])[:4]
    if len(left) < 2 or len(right) < 2:
        return False
    glue = " ".join(left + right)
    topic_tokens = " ".join(_norm_tokens(topic))
    if glue not in topic_tokens:
        return False
    return "…" not in topic and "..." not in topic and "," not in topic


def _subsequence(small: list[str], big: list[str]) -> bool:
    index = 0
    for token in big:
        if index < len(small) and token == small[index]:
            index += 1
    return index == len(small) and len(small) > 0


def _words_dropped(topic: str, orchestrator_lines: list[str]) -> bool:
    topic_tokens = _norm_tokens(topic)
    if not topic_tokens:
        return False
    for line in orchestrator_lines:
        line_tokens = _norm_tokens(line)
        if not line_tokens or topic_tokens == line_tokens:
            continue
        ratio = difflib.SequenceMatcher(
            None, " ".join(topic_tokens), " ".join(line_tokens)
        ).ratio()
        if ratio < 0.8:
            continue
        if len(topic_tokens) < len(line_tokens) and line_tokens[: len(topic_tokens)] == topic_tokens:
            return True
        if len(topic_tokens) < len(line_tokens) and _subsequence(topic_tokens, line_tokens):
            return True
    return False


def _english_fail(text: str) -> bool:
    if is_portuguese(text) or _PT_CHAR.search(text or ""):
        return True
    return len(_PT_EXTRA.findall(text or "")) >= 2


def _fourgrams(masked: str) -> list[tuple[str, ...]]:
    tokens = [token for token in masked.split() if token]
    grams = []
    for index in range(len(tokens) - 3):
        gram = tuple(tokens[index : index + 4])
        if all(_placeholder(token) for token in gram):
            continue
        grams.append(gram)
    return grams


def entry_failures(row: dict[str, Any]) -> list[str]:
    """Per-entry hard failures. Cross-post rules live in ``candidate_failures``."""
    if str(row.get("production_status") or "") not in {"", "ready"}:
        return []
    if str(row.get("production_scope") or "") == "out":
        return []
    review = row.get("quality_review") if isinstance(row.get("quality_review"), dict) else {}
    if str(review.get("status") or "") == "fail":
        return []
    fails: list[str] = []
    name = str(row.get("session_id") or "?")
    asker, answerer = seat_names(row)
    headline = str(_get(row, "platform_overrides.youtube.title") or (row.get("base_metadata") or {}).get("title") or "")
    match = _HEADLINE_RE.match(headline.strip())
    if not match:
        fails.append(f"title_format: {name}: {headline[:90]}")
    else:
        if match.group(1) != asker or match.group(2) != answerer or match.group(1) == match.group(2):
            fails.append(f"title_format: {name}: names {match.group(1)} vs {match.group(2)} speakers {asker} vs {answerer}")
        if len(headline) > 80:
            fails.append(f"title_format: {name}: {len(headline)} chars")
    turns = _read_turns(row)
    orchestrator = [str(item.get("text") or "") for item in turns if item.get("role") == "orchestrator"]
    topic = ""
    if " - " in headline:
        topic = headline.split(" - ", 1)[1]
    opening_exact = ""
    for item in turns:
        if str(item.get("role") or "") == "orchestrator" and str(item.get("text") or "").strip():
            opening_exact = str(item.get("text") or "").strip()
            break
    full_title = f"{asker} vs {answerer} - {opening_exact}" if asker and answerer and opening_exact else ""
    prefix_fit = (
        bool(topic)
        and bool(opening_exact)
        and len(_clean(full_title)) > 80
        and _norm_tokens(opening_exact)[: len(_norm_tokens(topic))] == _norm_tokens(topic)
    )
    if topic and not prefix_fit and (_words_dropped(topic, orchestrator) or any(_run_on(topic, line) for line in orchestrator)):
        fails.append(f"question_words_dropped: {name}: {topic[:90]}")
    if _dangling(topic) and _clean(topic) != _clean(opening_exact):
        fails.append(f"truncated_sentence: {name}: {topic[:90]}")
    qa = _qa(row)
    quote = str(qa.get("quote") or "")
    speaker = display_name(str(qa.get("quote_speaker") or ""))
    spoken = [(str(item.get("speaker") or ""), str(item.get("text") or "")) for item in turns]
    if quote:
        owners = [who for who, text in spoken if _quote_in_utterance(quote, text)]
        if not owners:
            fails.append(f"quote_verbatim: {name}: quote is not in the transcript")
        elif speaker and speaker not in {display_name(who) for who in owners}:
            fails.append(f"quote_verbatim: {name}: quote_speaker {speaker}")
    for platform in _FEED:
        caption = str(_get(row, _PLATFORMS[platform][0]) or "")
        found = [item for item in _quotes(caption) if len(_norm_tokens(item)) >= 4]
        if not found:
            fails.append(f"quote_verbatim: {name} [{platform}]: missing a 4-word quote")
            continue
        if not any(any(_quote_in_utterance(item, text) for _who, text in spoken) for item in found):
            fails.append(f"quote_verbatim: {name} [{platform}]: quote is not verbatim")
    for platform, (field, max_chars, hook_max, min_tags, max_tags) in _PLATFORMS.items():
        caption = str(_get(row, field) or "")
        if not caption.strip():
            fails.append(f"{platform}_empty: {name}")
            continue
        if len(caption) > max_chars:
            fails.append(f"{platform}_too_long: {name}: {len(caption)}>{max_chars}")
        lines = _lines(caption)
        if lines and len(lines[0]) > hook_max:
            fails.append(f"{platform}_hook_too_long: {name}: {len(lines[0])}")
        count = len(_tags(caption))
        if not (min_tags <= count <= max_tags):
            fails.append(f"hashtags: {name} [{platform}]: {count} tags")
        if _PLACEHOLDER.search(caption):
            fails.append(f"placeholder_or_template: {name} [{platform}]")
        lowered = _outside_quotes(caption).lower()
        for phrase in BANNED_PHRASES:
            if phrase in lowered:
                fails.append(f"banned_phrases: {name} [{platform}]: {phrase}")
        if _CAUGHT.search(caption):
            fails.append(f"banned_phrases: {name} [{platform}]: X caught Y")
        if "\u2014" in caption:
            fails.append(f"banned_phrases: {name} [{platform}]: em dash")
        if len(_EMOJI.findall(caption)) > 1:
            fails.append(f"banned_phrases: {name} [{platform}]: emoji spam")
        if lines and _english_fail(lines[0]):
            fails.append(f"english_only: {name} [{platform}]")
        for line in lines[1:]:
            if _english_fail(line):
                fails.append(f"english_only: {name} [{platform}]")
                break
            if line.endswith("?") and _dangling(line) and _clean(line) != _clean(orchestrator[0] if orchestrator else ""):
                fails.append(f"truncated_sentence: {name} [{platform}]: {line[-80:]}")
        if platform in _FEED and not any(line.endswith("?") for line in lines):
            fails.append(f"{platform}_no_question: {name}")
        if platform == "tiktok" and re.search(r"\bsubscribe\b", caption, re.IGNORECASE):
            fails.append(f"tiktok_says_subscribe: {name}")
        if platform in _FEED:
            if not lines or lines[0] != headline:
                fails.append(f"title_format: {name} [{platform}]: line 1 is not the headline")
            disclosures = _disclosure_lines(caption)
            if len(disclosures) != 1 or not _AI_CUE.search(disclosures[0] if disclosures else ""):
                fails.append(f"disclosure: {name} [{platform}]")
        if platform == "linkedin" and lines and len(lines[0]) > 150:
            fails.append(f"linkedin_hook_too_long: {name}")
        verdict_zone = _outside_quotes(caption)
        if _VERDICT.search(verdict_zone):
            evidence = str(qa.get("verdict_evidence") or "")
            if not evidence or not any(_quote_in_utterance(evidence, text) or _ws(evidence) in _ws(text) for _who, text in spoken):
                fails.append(f"banned_phrases: {name} [{platform}]: verdict word without evidence")
    allowed = {tag.lower() for tag in allowed_hashtags(row)}
    model_tags = {"#gemini", "#llama", "#chatgpt", "#deepseek", "#claude"}
    topic_or_model = model_tags | {tag.lower() for tag in allowed_hashtags(row) if tag.lower() not in {"#ai", "#tech", "#artificialintelligence", "#aidebate"}}
    for platform in _FEED:
        tags = _tags(str(_get(row, _PLATFORMS[platform][0]) or ""))
        if len(tags) != 3:
            continue
        if any(tag.lower() not in allowed for tag in tags):
            fails.append(f"hashtags: {name} [{platform}]: tag outside the allowed set {tags}")
        if not any(tag.lower() in topic_or_model for tag in tags):
            fails.append(f"hashtags: {name} [{platform}]: no topic or model tag")
    present = {seat_names(row)[0].lower(), seat_names(row)[1].lower()}
    tag_for = {"gemini": "#gemini", "llama": "#llama", "gpt-4o": "#chatgpt", "deepseek": "#deepseek", "claude": "#claude"}
    blob = " ".join(str(_get(row, field) or "") for field, *_rest in _PLATFORMS.values())
    for family, tag in tag_for.items():
        if tag in blob.lower() and family not in present:
            fails.append(f"hashtags: {name}: {tag} but {family} is not in the video")
    pin_title = str(_get(row, "platform_overrides.pinterest.title") or "")
    pin_desc = str(_get(row, "platform_overrides.pinterest.description") or "")
    if len(pin_title) > 100:
        fails.append(f"pinterest_title_too_long: {name}")
    if len(pin_desc) > 500:
        fails.append(f"pinterest_description_too_long: {name}")
    angle = str(qa.get("angle") or "")
    if angle not in set("ABCD"):
        fails.append(f"angle_rotation: {name}: angle {angle or 'missing'}")
    for platform in ("tiktok", "youtube", "instagram", "facebook", "kwai"):
        if _get(row, f"platform_overrides.{platform}.ai_generated") is not True:
            fails.append(f"ai_disclosure_flag_missing: {name} [{platform}]")
    if asker and answerer and asker == answerer:
        fails.append(f"same_model_both_seats: {name}")
    fams = {display_name(item).lower() for item in _MODEL_RE.findall(blob + " " + headline) if display_name(item)}
    real = {name for name in (asker.lower(), answerer.lower()) if name}
    extra = fams - real
    if extra:
        fails.append(f"model_name_not_in_transcript: {name}: {sorted(extra)}")
    caps = {platform: str(_get(row, field) or "").strip() for platform, (field, *_rest) in _PLATFORMS.items()}
    seen: dict[str, str] = {}
    for platform, caption in caps.items():
        if caption and caption in seen:
            fails.append(f"identical_across_platforms: {name}: {platform} == {seen[caption]}")
        elif caption:
            seen[caption] = platform
    closer = _closing(str(_get(row, _PLATFORMS["tiktok"][0]) or ""))
    if closer.strip().lower() in _GENERIC_CLOSERS:
        fails.append(f"unique_closers: {name}: {closer}")
    opening = ""
    for item in turns:
        if str(item.get("role") or "") == "orchestrator" and str(item.get("text") or "").strip():
            opening = _clean(str(item.get("text") or ""))
            break
    if asker and answerer and opening:
        fitted = f"{asker} vs {answerer} - {opening}"
        banned_opening = any(phrase in opening.lower() for phrase in BANNED_PHRASES)
        if len(fitted) <= 80 and _clean(topic) != opening and not banned_opening:
            fails.append(f"title_not_original_question: {name}: {topic[:90]}")
    if "what should a viewer ask" in (topic or "").lower() or "what should a viewer ask" in headline.lower():
        fails.append(f"template_title: {name}")
    if closer and any(closer.strip().lower().rstrip("?.") == str(item.get("text") or "").strip().lower().rstrip("?.") for item in turns):
        fails.append(f"closer_pasted_debate_line: {name}")
    if closer.count('"') % 2 == 1 or closer.count("\u201c") != closer.count("\u201d"):
        fails.append(f"closer_garbled: {name}")
    for platform in _FEED:
        caption = str(_get(row, _PLATFORMS[platform][0]) or "")
        lines = _lines(caption)
        quote_lines = [line for line in lines if line.startswith('"') and line.endswith('"')]
        if not quote_lines:
            fails.append(f"quote_line: {name} [{platform}]: quote is not on its own line")
        elif len(quote_lines[0].strip('"').split()) > 20:
            fails.append(f"quote_line: {name} [{platform}]: quote is over 20 words")
        if lines:
            follow = ""
            for line in lines[1:]:
                if line.startswith('"'):
                    break
                follow = line
                break
            if follow and len(follow.split()) > 12:
                fails.append(f"context_line: {name} [{platform}]: {len(follow.split())} words")
        if any(tag.lower() in {item.lower() for item in BROAD_TAGS} for tag in _tags(caption)):
            fails.append(f"hashtags: {name} [{platform}]: broad tag banned")
        if "\u2014" in caption or "\u2013" in caption:
            fails.append(f"banned_phrases: {name} [{platform}]: em dash")
    return fails


def _active(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return posting_order([row for row in rows if _ready_ok(row)])


def _cross_failures(rows: list[dict[str, Any]], *, expected: int | None = None) -> list[str]:
    active = _active(rows)
    if expected is None:
        ready_count = sum(
            1
            for row in rows
            if str(row.get("production_status") or "") == "ready"
            and str(row.get("production_scope") or "") != "out"
            and str((row.get("quality_review") or {}).get("status") or "") != "fail"
        )
        expected = ready_count or len(active)
    expected = max(int(expected), 1)
    fails: list[str] = []
    angles = [str(_qa(row).get("angle") or "") for row in active]
    for index in range(1, len(angles)):
        if angles[index] and angles[index] == angles[index - 1]:
            fails.append(f"angle_rotation: consecutive {angles[index]}")
            break
    for index in range(len(angles) - 3):
        window = angles[index : index + 4]
        if all(window) and len(set(window)) < 3:
            fails.append("angle_rotation: 4-post window has fewer than 3 angles")
            break
    disclosures = [_disclosure_lines(str(_get(row, _PLATFORMS["tiktok"][0]) or "")) for row in active]
    flat = [item[0].strip().lower() for item in disclosures if item]
    for index in range(len(flat)):
        window = flat[index : index + 10]
        if len(window) >= 2 and len(set(window)) < len(window):
            if any(window.count(item) > 1 for item in window):
                fails.append("disclosure: repeated inside a 10-post window")
                break
    counts = collections.Counter(flat)
    for text, count in counts.items():
        if count >= 3 and count / expected > 0.05:
            fails.append(f"disclosure: shared by {count} posts")
            break
    closers: dict[str, int] = collections.defaultdict(int)
    for row in active:
        closer = _closing(str(_get(row, _PLATFORMS["tiktok"][0]) or "")).strip().lower()
        if closer:
            closers[closer] += 1
    for closer, count in closers.items():
        if count > 1:
            fails.append(f"unique_closers: {count} posts share {closer[:80]}")
            break
    closer_list = list(closers)
    for left in range(len(closer_list)):
        for right in range(left + 1, len(closer_list)):
            ratio = difflib.SequenceMatcher(None, closer_list[left], closer_list[right]).ratio()
            if ratio >= 0.7:
                fails.append(f"unique_closers: similarity {ratio:.2f}")
                break
        else:
            continue
        break
    for platform in _FEED:
        field = _PLATFORMS[platform][0]
        openings: list[str] = []
        closings: list[str] = []
        positioned: list[list[str]] = []
        grams: collections.Counter[tuple[str, ...]] = collections.Counter()
        tag_sets: collections.Counter[tuple[str, ...]] = collections.Counter()
        tag_freq: collections.Counter[str] = collections.Counter()
        raw_lines: collections.defaultdict[str, set[str]] = collections.defaultdict(set)
        for row in active:
            caption = str(_get(row, field) or "")
            sid = str(row.get("session_id") or "")
            sentences = [item for item in _body_sentences(caption) if _editorial(_mask(item, row))]
            masked = [_mask(item, row) for item in sentences]
            positioned.append(masked)
            if masked:
                openings.append(masked[0])
            closing = _closing(caption)
            if closing and _editorial(_mask(closing, row)):
                closings.append(_mask(closing, row))
            for sentence in sentences:
                grams.update(_fourgrams(_mask(sentence, row)))
            signature = tuple(sorted(tag.lower() for tag in _tags(caption)))
            if signature:
                tag_sets[signature] += 1
                tag_freq.update(signature)
            for line in _lines(caption):
                if len(line) >= 20:
                    raw_lines[_MODEL_RE.sub("<M>", line.lower()).strip()].add(sid)
        if len(openings) != len(set(openings)):
            fails.append(f"skeleton_similarity: {platform} opening repeats")
        if len(closings) != len(set(closings)):
            repeated = collections.Counter(closings).most_common(1)
            fails.append(f"skeleton_similarity: {platform} closing repeats {repeated}")
        for left in range(len(positioned)):
            for right in range(left + 1, len(positioned)):
                limit = min(len(positioned[left]), len(positioned[right]))
                hit = False
                for pos in range(limit):
                    ratio = difflib.SequenceMatcher(
                        None, positioned[left][pos], positioned[right][pos]
                    ).ratio()
                    if ratio >= 0.75:
                        fails.append(f"skeleton_similarity: {platform} sentence {pos} ratio {ratio:.2f}")
                        hit = True
                        break
                if hit:
                    break
            else:
                continue
            break
        for gram, count in grams.items():
            if count >= 3 and count / expected > 0.05:
                fails.append(f"skeleton_similarity: {platform} 4-gram {' '.join(gram)} in {count} posts")
                break
        for signature, count in tag_sets.items():
            if count > 12:
                fails.append(f"hashtags: {platform} set used {count} times")
                break
        exempt = {"#ai", "#shorts", "#gemini", "#llama", "#chatgpt", "#deepseek", "#claude", "#aiconsciousness", "#philosophy", "#aiethics", "#aialignment", "#bigtech", "#dataprivacy", "#aihallucination", "#futureofwork"}
        for tag, count in tag_freq.items():
            if tag not in exempt and count >= 3 and count / expected > 0.4:
                fails.append(f"hashtags: {platform} {tag} in {count}/{expected}")
                break
        for line, users in raw_lines.items():
            if len(users) > 1:
                fails.append(f"{platform}_duplicate_line: {line[:80]}")
                break
    return fails


def candidate_failures(
    row: dict[str, Any],
    peers: list[dict[str, Any]] | None = None,
    *,
    expected: int = 1,
) -> list[str]:
    """Entry rules plus cross rules against posts already accepted."""
    return entry_failures(row) + _cross_failures([*(peers or []), row], expected=expected)


def status_gate_failures(rows: list[dict[str, Any]]) -> list[str]:
    fails: list[str] = []
    needs: list[str] = []
    for row in rows:
        sid = str(row.get("session_id") or "?")
        status = str(row.get("production_status") or "")
        if status not in PRODUCTION_STATUS_VALUES:
            fails.append(f"status_gate: {sid}: production_status {status or 'missing'}")
            continue
        qa = _qa(row)
        qa_status = str(qa.get("status") or "")
        if status == "ready":
            if str(row.get("production_scope") or "") == "out":
                continue
            review = row.get("quality_review") if isinstance(row.get("quality_review"), dict) else {}
            if str(review.get("status") or "") == "fail":
                continue
            if qa_status == "needs_review":
                needs.append(f"{sid}: {qa.get('reason') or 'needs_review'}")
                continue
            if qa_status != "ok" or str(qa.get("generator") or "") != "captions_v4" or not str(qa.get("model") or "").strip():
                fails.append(f"status_gate: {sid}: caption_qa {qa_status or 'missing'} generator={qa.get('generator')}")
            continue
        if not isinstance(row.get("legacy_captions"), dict) or not _channel_metadata_only(row):
            fails.append(f"status_gate: {sid}: non-ready captions were not parked")
    return fails


def validate_library(rows: list[dict[str, Any]]) -> tuple[int, dict[str, list[str]]]:
    """Return ``(exit_code, failures_by_rule)``. ``needs_review`` is not a failure."""
    grouped: dict[str, list[str]] = collections.defaultdict(list)
    for item in status_gate_failures(rows):
        grouped["status_gate"].append(item)
    ready = [row for row in rows if str(row.get("production_status") or "") == "ready" and str(_qa(row).get("status") or "") != "needs_review"]
    # Content rules apply only to rows that claim to be publishable. A ready
    # row that is not ok already failed status_gate.
    for row in ready:
        if not _ready_ok(row):
            continue
        for item in entry_failures(row):
            rule = item.split(":", 1)[0]
            grouped[rule].append(item)
    for item in _cross_failures(rows):
        rule = item.split(":", 1)[0]
        grouped[rule].append(item)
    code = 1 if any(grouped.values()) else 0
    return code, dict(grouped)


def needs_review_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    found = []
    for row in rows:
        if str(row.get("production_status") or "") == "ready" and str(_qa(row).get("status") or "") == "needs_review":
            found.append(row)
    return found


def require_valid_library(rows: list[dict[str, Any]]) -> None:
    """Abort the caller when the library fails. Exporters call this first."""
    code, grouped = validate_library(rows)
    if code == 0:
        return
    total = sum(len(items) for items in grouped.values())
    print(f"HARD FAILURES: {total} across {len(grouped)} rules", file=sys.stderr)
    for rule, items in sorted(grouped.items(), key=lambda kv: -len(kv[1])):
        print(f"  FAIL {rule}: {len(items)}", file=sys.stderr)
        for message in items[:5]:
            print(f"      - {message}", file=sys.stderr)
    raise SystemExit(code)


def _load_ids(path: str) -> set[str]:
    raw = Path(path).read_text(encoding="utf-8")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {line.strip() for line in raw.splitlines() if line.strip()}
    if isinstance(payload, dict):
        payload = payload.get("real_priority", [])
    return {(item.get("session_id") if isinstance(item, dict) else str(item)) for item in payload}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="validate_aiwake_captions")
    parser.add_argument("library", nargs="?", default="channels_config/aiwake/store/content_library.json")
    parser.add_argument("--ids")
    parser.add_argument("--max-examples", type=int, default=5)
    args = parser.parse_args(argv)
    library = json.loads(Path(args.library).read_text(encoding="utf-8"))
    if args.ids:
        wanted = _load_ids(args.ids)
        total = len(library)
        library = [row for row in library if row.get("session_id") in wanted]
        missing = wanted - {row.get("session_id") for row in library}
        print(f"Scope: {len(library)}/{total} entries from {args.ids}" + (f" ({len(missing)} ids not found!)" if missing else ""))
    code, grouped = validate_library(library)
    review = needs_review_rows(library)
    active = _active(library)
    print(f"validator {VALIDATOR_VERSION}")
    print(f"Entries: {len(library)} (publishable {len(active)}, needs_review {len(review)})")
    if review:
        print("NEEDS REVIEW (excluded, not a failure):")
        for row in review[: args.max_examples]:
            print(f"  - {row.get('session_id')}: {_qa(row).get('reason')}")
    total = sum(len(items) for items in grouped.values())
    print(f"HARD FAILURES: {total} across {len(grouped)} rules")
    for rule, items in sorted(grouped.items(), key=lambda kv: -len(kv[1])):
        print(f"  FAIL {rule}: {len(items)}")
        for message in items[: args.max_examples]:
            print(f"      - {message}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
