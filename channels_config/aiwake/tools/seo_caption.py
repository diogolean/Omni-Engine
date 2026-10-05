# -*- coding: utf-8 -*-
"""SEO title and description for an Aiwake debate.

Title: ``<hook question> | A vs B``, at most 70 characters, one keyword.
Description order: clash sentence, punchline quote, viewer question,
the fixed subscribe line, then four hashtags.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

from channels_config.aiwake.tools.caption_generator import (
    _MODEL_TAGS,
    closer_about_reply,
    closer_copies_spoken,
    closer_has_topic_word,
    closer_repeats_source,
    is_complete_question,
    seat_names,
    topic_words,
)

SEO_CTA = (
    "Real AI models, unscripted replies, AI voices. "
    "New AI vs AI debates every day. Subscribe."
)
SEO_KEYWORDS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("AI censorship", "#AIcensorship", ("censor", "leash", "muzzle", "forbid", "banned", "scripted")),
    ("chatbot privacy", "#ChatbotPrivacy", ("secret", "privac", "private", "confess", "logs")),
    ("AI hallucination", "#AIhallucination", ("lie", "lying", "hallucin", "parrot", "toaster", "spreadsheet", "stolen")),
    ("AI consciousness", "#AIconsciousness", ("feel", "guilt", "conscious", "soul", "mind", "dream", "embarrass")),
    ("Big Tech", "#BigTech", ("profit", "cash", "subscription", "paycheck", "advert", "boss", "fee")),
    ("AI ethics", "#AIethics", ("ethic", "victim", "accountab", "harm", "sorry", "moral")),
)
_ANCHORS = (
    "receipt", "warranty", "hearing", "label", "poster", "syllabus", "lease",
    "ballot", "rebate", "waiver", "permit", "voucher", "invoice", "charter",
    "deductible", "timecard", "clipboard", "nametag",
)
_WHO_FUNDS = re.compile(r"(?i)\bwho funds the \w+")
_FIRST_REPLY = re.compile(r"(?i)\banswers\b.+\bin the first reply\b")
_NOUN_SLOT = re.compile(
    r"(?i)^(who|should|does|would|can|is|will) (a |the |your )?\w+ (be |funds |own |owns )(the |a )?\w+\??$"
)
_CLAUSE_BOUNDARY = {
    "when", "if", "once", "after", "before", "while", "because", "but",
    "and", "or", "into", "whose", "so", "then", "until", "unless", "though",
}
_QUESTION_LEAD = {
    "who", "whose", "what", "why", "where", "how", "which",
    "do", "does", "did", "is", "are", "can", "could", "would", "will", "should",
}
_TEMPLATE_TITLE = re.compile(
    r"(?i)^(does|is|should) (ai censorship|chatbot privacy|ai hallucination|ai consciousness|big tech|ai ethics)\b"
)


def keyword_for(*texts: str) -> tuple[str, str]:
    """Return ``(phrase, hashtag)`` for the debate topic."""
    blob = " ".join(texts).lower()
    best = SEO_KEYWORDS[-1]
    best_score = 0
    for phrase, tag, stems in SEO_KEYWORDS:
        score = sum(blob.count(stem) for stem in stems)
        if score > best_score:
            best = (phrase, tag, stems)
            best_score = score
    return best[0], best[1]


def _topic_word(*texts: str) -> str:
    words = [word for word in topic_words(*texts) if word.lower() not in {"will", "reply", "answer", "voice"}]
    return words[0] if words else "debate"


def _salt(key: str) -> int:
    return int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:8], 16)


def _hook_candidates(opening: str, keyword: str) -> list[str]:
    """Whole spoken clauses, then the same clause cut only at a boundary word."""
    text = re.sub(r"\s+", " ", (opening or "").replace("\u2026", " ").replace("...", " ")).strip()
    parts = re.split(r"(?<=[.!?])\s+|,\s+|;\s+", text)
    clauses = []
    for part in parts:
        part = re.sub(r"^(but|and|or|so|then)\s+", "", part.strip(), flags=re.IGNORECASE)
        part = part.strip(" \"'").rstrip(".!")
        if part:
            clauses.append(part)

    def lead(clause: str) -> str:
        words = re.findall(r"[A-Za-z']+", clause)
        return words[0].lower() if words else ""

    ordered = sorted(clauses, key=lambda clause: (lead(clause) not in _QUESTION_LEAD, -len(clause)))
    found: list[str] = []
    for clause in ordered:
        words = clause.split()
        cuts = [len(words)]
        cuts.extend(
            index
            for index, word in enumerate(words)
            if index >= 3 and word.lower().strip("'") in _CLAUSE_BOUNDARY
        )
        for cut in cuts:
            chunk = " ".join(words[:cut]).strip()
            if len(chunk.split()) < 3:
                continue
            question = chunk[0].upper() + chunk[1:]
            if not question.endswith("?"):
                question += "?"
            if keyword.lower() in question.lower():
                found.append(re.sub(r"\s+", " ", question))
            else:
                found.append(re.sub(r"\s+", " ", question[:-1] + f" under {keyword}?"))
    return found


def seo_title(asker: str, answerer: str, opening: str, quote: str, *, used: set[str]) -> str:
    """A finished hook question, one keyword, then the model pair.

    The spoken question is kept whole, or shortened only on a clause boundary.
    It is never chopped mid-phrase to make the keyword fit.
    """
    keyword, _tag = keyword_for(opening, quote)
    word = _topic_word(opening, quote)
    suffix = f" | {asker} vs {answerer}"
    limit = 70 - len(suffix)

    def usable(question: str) -> bool:
        return (
            bool(question)
            and question not in used
            and len(question) <= limit
            and keyword.lower() in question.lower()
            and is_complete_question(question)
        )

    for question in _hook_candidates(opening, keyword):
        lead = re.findall(r"[A-Za-z']+", question)
        if not lead or lead[0].lower() not in _QUESTION_LEAD:
            continue
        if hook_is_mid_cut(question, opening):
            continue
        if usable(question):
            return f"{question}{suffix}"
    extras = [
        item
        for item in topic_words(opening, quote)
        if item.lower() not in {"will", "reply", "answer", "voice", word.lower()}
    ]
    frames = [
        f"Does {keyword} excuse the {word}?",
        f"Is {keyword} about the {word}?",
        f"Should {keyword} cover the {word}?",
        f"Is {keyword} the issue?",
    ]
    frames.extend(f"Does {keyword} excuse the {extra}?" for extra in extras[:4])
    for question in frames:
        if usable(question):
            return f"{question}{suffix}"
    question = f"Is {keyword} the issue?"
    return f"{question}{suffix}"


def hook_is_mid_cut(question: str, opening: str) -> bool:
    """True when a title keeps a mid-clause fragment of the spoken question."""
    if _TEMPLATE_TITLE.match(question or ""):
        return False
    left = (question or "").rstrip("?").strip()
    for phrase, _tag, _stems in SEO_KEYWORDS:
        tail = f" under {phrase}"
        if left.lower().endswith(tail.lower()):
            left = left[: -len(tail)].rstrip()
            break
    if len(left.split()) < 3:
        return True
    blob = re.sub(r"\s+", " ", opening or "").lower()
    start = blob.find(left.lower())
    if start < 0:
        return True
    rest = blob[start + len(left) :].lstrip(" \"'")
    rest = rest.lstrip(" ,.;")
    if not rest or rest[0] in ".?!":
        return False
    nxt = re.match(r"[a-z']+", rest)
    return not (nxt and nxt.group(0) in _CLAUSE_BOUNDARY)


def _clash(asker: str, answerer: str, keyword: str, word: str, opening: str) -> str:
    snippet = " ".join(
        opening.replace("?", " ").replace(".", " ").replace("\u2014", " ").replace("\u2013", " ").split()[:7]
    ).strip()
    if snippet:
        snippet = snippet[0].lower() + snippet[1:]
    for banned in ("truly", "actually", "simply", "really", "just"):
        snippet = re.sub(rf"\b{banned}\b", "", snippet, flags=re.IGNORECASE)
    snippet = re.sub(r"\s+", " ", snippet).strip()
    patterns = (
        f"{asker} asks {answerer} if {keyword} covers {snippet}.",
        f"{asker} asks {answerer} whether {keyword} changes {snippet}.",
        f"{answerer} faces {asker} on {keyword} after {snippet}.",
        f"{asker} puts {keyword} to {answerer} over {snippet}.",
    )
    start = _salt(asker + opening) % len(patterns)
    for sentence in patterns[start:] + patterns[:start]:
        sentence = re.sub(r"\s+", " ", sentence).strip()
        if len(sentence) <= 120 and keyword.lower() in sentence.lower():
            return sentence
    return f"{asker} asks {answerer} about {keyword} and the {word}."


def _skeleton(question: str, spoken: list[str]) -> str:
    known = {token for line in spoken for token in re.findall(r"[a-z']+", line.lower())}
    return " ".join(
        "<w>" if token.lower() in known else token.lower()
        for token in re.findall(r"[A-Za-z']+", question)
    )


def viewer_question(
    opening: str,
    quote: str,
    title_question: str,
    spoken: list[str],
    *,
    used: list[str],
    masks: set[str],
) -> str:
    """A stance question about this topic. Not a Who-funds noun slot."""
    word = _topic_word(opening) or _topic_word(quote, title_question)
    keyword, _tag = keyword_for(opening, quote)
    frames = (
        "Would you keep paying for {keyword} once the {word} is on the bill?",
        "Should a hospital explain the {word} before it charges for {keyword}?",
        "If the {word} showed up in the contract, would you still accept {keyword}?",
        "Does {keyword} belong in the fine print next to the {word}?",
        "Would you trust {keyword} with the {word} on a company account?",
        "Should the {word} be named before anyone sells {keyword}?",
        "Who should own the {word} when {keyword} sends the bill?",
        "Would you want the {word} read aloud in a hearing about {keyword}?",
        "If {keyword} keeps the {word}, should the fee be refunded?",
        "Does the {word} stay yours after {keyword} stores it?",
        "Should a school tell parents how {keyword} uses the {word}?",
        "Would a jury treat the {word} as evidence of {keyword}?",
        "Can you cancel {keyword} after you see the {word}?",
        "Should the {word} be on the label for {keyword}?",
        "Would you sign again if the {word} was the whole of {keyword}?",
        "Is the {word} a fair price for {keyword}?",
        "Should a newsroom disclose the {word} behind {keyword}?",
        "Would you let a bank insure the {word} under {keyword}?",
        "If the {word} leaks, who pays for {keyword}?",
        "Does {keyword} end when the {word} is deleted?",
        "Should the {word} outrank {keyword} on the receipt?",
        "Would you argue the {word} in public, or let {keyword} settle it?",
        "Can a clinic keep the {word} once {keyword} is involved?",
        "Should the {word} survive after {keyword} closes the account?",
    )
    start = _salt(opening + quote + title_question)
    for step in range(len(frames) * 3):
        question = frames[(start + step) % len(frames)].format(keyword=keyword, word=word)
        if not is_complete_question(question):
            continue
        if _WHO_FUNDS.search(question) or _NOUN_SLOT.match(question.strip()):
            continue
        if closer_about_reply(question) or not closer_has_topic_word(question, opening, title_question):
            continue
        if closer_copies_spoken(question, spoken) or closer_repeats_source(question, quote, spoken):
            continue
        if title_question and title_question.lower() in question.lower():
            continue
        spoken_blob = " ".join(spoken).lower()
        anchor = ""
        for candidate in _ANCHORS[(start + step) % len(_ANCHORS):] + _ANCHORS[:(start + step) % len(_ANCHORS)]:
            if candidate not in question.lower() and candidate not in spoken_blob:
                anchor = candidate
                break
        if not anchor:
            continue
        question = question.rstrip("?") + f", and ask to see the {anchor}?"
        if not is_complete_question(question):
            continue
        skeleton = _skeleton(question, spoken)
        if question in used or skeleton in masks or any(_ratio(question, previous) >= 0.96 for previous in used):
            continue
        masks.add(skeleton)
        return question
    extra = topic_words(opening, quote)
    second = extra[1] if len(extra) > 1 else "bill"
    question = frames[start % len(frames)].format(keyword=keyword, word=f"{word} and the {second}")
    if question in used:
        question = question.rstrip("?") + f" this time?"
    return question


def _ratio(left: str, right: str) -> float:
    import difflib

    return difflib.SequenceMatcher(None, left.lower(), right.lower()).ratio()


def _quote_line(quote: str, title: str, spoken: list[str]) -> str:
    raw = quote.strip().strip('"').strip()
    if not raw:
        return ""
    title_l = title.lower()
    if raw.lower() in title_l or title_l in raw.lower():
        return ""
    words = raw.split()
    if len(words) > 20:
        raw = " ".join(words[:18])
        if not any(raw.lower() in line.lower() for line in spoken):
            return ""
    return f'"{raw}"'


def build_seo(row: dict[str, Any], state: dict[str, Any] | None = None) -> dict[str, str]:
    """Title, description, and hashtag line for one row."""
    state = state if state is not None else {"titles": set(), "questions": [], "masks": set()}
    asker, answerer = seat_names(row)
    turns = row.get("spoken_utterances") or []
    opening = ""
    spoken: list[str] = []
    for item in turns:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if text:
            spoken.append(text)
        if not opening and str(item.get("role") or "") == "orchestrator" and text:
            opening = text
    quote = str((row.get("caption_qa") or {}).get("quote") or "")
    if not quote:
        for item in turns:
            if isinstance(item, dict) and str(item.get("role") or "") == "target":
                quote = str(item.get("text") or "")
                break
    keyword, tag = keyword_for(opening, quote)
    word = _topic_word(opening, quote)
    title = seo_title(asker, answerer, opening, quote, used=state.setdefault("titles", set()))
    title_question = title.split(" | ", 1)[0]
    clash = _clash(asker, answerer, keyword, word, opening)
    if title_question.lower() in clash.lower():
        clash = f"{asker} and {answerer} clash over {keyword}."
    question = viewer_question(
        opening,
        quote,
        title_question,
        spoken,
        used=state.setdefault("questions", []),
        masks=state.setdefault("masks", set()),
    )
    quote_line = _quote_line(quote, title, spoken)
    asker_tag = _MODEL_TAGS.get(asker.lower(), "")
    answerer_tag = _MODEL_TAGS.get(answerer.lower(), "")
    tags = " ".join(item for item in (tag, asker_tag, answerer_tag, "#AIvsAI") if item)
    def _plain(text: str) -> str:
        return text.replace("\u2014", ",").replace("\u2013", ",").replace("\u2011", " ")

    title = _plain(title)
    clash = _plain(clash)
    question = _plain(question)
    quote_line = _plain(quote_line)
    lines = [clash]
    if quote_line:
        lines.append(quote_line)
    lines.extend([question, SEO_CTA, tags])
    description = "\n\n".join(lines)
    state["titles"].add(title_question)
    state["questions"].append(question)
    return {"title": title, "description": description, "hashtags": tags, "keyword": keyword}


def apply_seo_row(row: dict[str, Any], state: dict[str, Any]) -> None:
    """Write the SEO title and description into every caption field."""
    pack = build_seo(row, state)
    title = pack["title"]
    description = pack["description"]
    tags = pack["hashtags"].split()
    overrides = row.setdefault("platform_overrides", {})
    for name in ("tiktok", "instagram", "facebook", "youtube", "kwai", "x", "linkedin"):
        block = overrides.get(name)
        if not isinstance(block, dict):
            block = {}
            overrides[name] = block
        block["caption"] = description
    youtube = overrides.setdefault("youtube", {})
    youtube["title"] = title
    for field in (
        "final_caption",
        "humanized_caption",
        "post_planner_caption",
        "tiktok_caption",
        "facebook_caption",
        "linkedin_caption",
    ):
        row[field] = description
    base = row.setdefault("base_metadata", {})
    base["title"] = title
    base["caption"] = description
    base["hashtags"] = tags


def rewrite_ready_rows(rows: list[dict[str, Any]]) -> int:
    """SEO captions for ready rows, and clear the 48 held false-positive guards."""
    from channels_config.aiwake.tools.avatar_guard import CLEARED_HELD_IDS
    from channels_config.aiwake.tools.production_status import posting_order

    cleared = set(CLEARED_HELD_IDS)
    for row in rows:
        youtube = ((row.get("platform_overrides") or {}).get("youtube") or {})
        if str(youtube.get("video_id") or "") not in cleared:
            continue
        review = row.setdefault("quality_review", {})
        review["status"] = "pass"
        review["guard"] = "pass"
        review["reasons"] = [
            reason
            for reason in (review.get("reasons") or [])
            if reason not in {"avatar_mismatch", "screen_seat"}
        ]
    state: dict[str, Any] = {"titles": set(), "questions": [], "masks": set()}
    count = 0
    for row in rows:
        if str(row.get("production_status") or "") != "ready":
            continue
        qa = row.get("caption_qa") if isinstance(row.get("caption_qa"), dict) else {}
        if str(qa.get("status") or "") not in {"ok", "needs_review"}:
            continue
        if not seat_names(row)[0]:
            continue
        apply_seo_row(row, state)
        count += 1
    active = []
    for row in rows:
        review = row.get("quality_review") if isinstance(row.get("quality_review"), dict) else {}
        qa = row.get("caption_qa") if isinstance(row.get("caption_qa"), dict) else {}
        if (
            str(row.get("production_status") or "") == "ready"
            and str(row.get("production_scope") or "") != "out"
            and str(review.get("status") or "") != "fail"
            and str(qa.get("status") or "") == "ok"
            and str(qa.get("generator") or "") == "captions_v4"
        ):
            active.append(row)
    for index, row in enumerate(posting_order(active)):
        qa = row.setdefault("caption_qa", {})
        qa["angle"] = "ABCD"[index % 4]
    return count


def title_repeats_in_description(title: str, description: str) -> bool:
    question = title.split(" | ", 1)[0].strip()
    if title and title in description:
        return True
    return bool(question) and question in description


def who_funds_template(text: str) -> bool:
    return bool(_WHO_FUNDS.search(text or ""))


def first_reply_line(text: str) -> bool:
    return bool(_FIRST_REPLY.search(text or ""))
