# -*- coding: utf-8 -*-
"""Card and description copy driven by the channel ``pin_theme`` strings.

The layout stays the original pin (serif hook panel, gold divider, CTA
bar). The words are config so they can be tuned without a code change.
New pins carry the article link. Nothing here updates a pin that already
exists on the board.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit


def theme_string(name: str) -> str:
    """A text field from the channel ``pin_theme``. Colors live beside these."""
    from production.formats.pins import config as cfg  # noqa: PLC0415

    raw = (getattr(cfg, "PIN_THEME", None) or {}).get(name)
    if isinstance(raw, str):
        return raw.strip()
    return ""


def _fill(template: str, **values: str) -> str:
    class _Blank(dict):
        def __missing__(self, key: str) -> str:
            return ""

    return template.format_map(_Blank({key: value or "" for key, value in values.items()})).strip()


_TITLE_STOPWORDS = frozenset(
    {"the", "and", "for", "with", "from", "that", "this", "into", "over"}
)


def _topic_already_named(title: str, topic: str) -> bool:
    """True when *title* already carries the topic, including after a colon."""
    title_l = title.lower()
    topic_l = (topic or "").strip().lower()
    if not topic_l:
        return False
    if topic_l in title_l:
        return True
    words = [
        word
        for word in re.split(r"[^a-z0-9']+", topic_l)
        if len(word) > 2 and word not in _TITLE_STOPWORDS
    ]
    return bool(words) and all(word in title_l for word in words)


def _collapse_repeated_title(text: str) -> str:
    """Drop a second copy of the same title, including one already cut at 100 characters.

    ``Giants: Giants`` becomes ``Giants``. A doubled clause such as
    ``Giants in History: Myths, Fossils and Viral Hoaxes`` repeated, then
    sliced mid-phrase, keeps the first copy only.
    """
    raw = (text or "").strip()
    if not raw:
        return ""
    spans = list(re.finditer(r"[A-Za-z0-9']+", raw))
    folded = [span.group(0).lower() for span in spans]
    count = len(folded)
    best_cut = 0
    if count >= 4:
        for start in range(3, count):
            remain = count - start
            if remain < 3 or remain > start:
                continue
            left = folded[:remain]
            right = folded[start : start + remain]
            same = left == right
            clipped = (
                left[:-1] == right[:-1]
                and len(right[-1]) >= 3
                and left[-1].startswith(right[-1])
            )
            if (same or clipped) and start > best_cut:
                best_cut = start
    if best_cut:
        return raw[: spans[best_cut - 1].end()].strip(" ,;:|-—–")
    parts = re.split(r"\s*[:|—–\-]\s*", raw, maxsplit=1)
    if len(parts) == 2:
        left, right = parts[0].strip(), parts[1].strip()
        if left and left.lower() == right.lower():
            return left
        if left and right.lower().startswith(left.lower()):
            rest = right[len(left) :].strip(" :-")
            return f"{left} {rest}".strip() if rest else left
    words = raw.split()
    if len(words) >= 2 and len(words) % 2 == 0:
        half = len(words) // 2
        if [word.lower().strip(":,;") for word in words[:half]] == [
            word.lower().strip(":,;") for word in words[half:]
        ]:
            return " ".join(words[:half]).strip(" ,;:|-—–")
    return raw


_CAPTION_LIMIT = 500


def keyword_first_title(title: str, topic: str) -> str:
    """Search title, subject first, at most 100 characters. No template suffix.

    The topic is not prepended when the title already names it. A title that
    would pass 100 characters ends on a word boundary.
    """
    from shared.pin_text import sanitize_pin_title, title_case_phrase  # noqa: PLC0415

    cleaned = sanitize_pin_title(_collapse_repeated_title(title))
    subject = sanitize_pin_title(title_case_phrase(topic))
    if not cleaned:
        return _collapse_repeated_title(subject)
    if _topic_already_named(cleaned, subject):
        return _collapse_repeated_title(cleaned)
    head = ""
    for word in re.split(r"[^A-Za-z0-9']+", subject.lower()):
        if len(word) > 2 and word not in _TITLE_STOPWORDS:
            head = word
            break
    if head and head not in cleaned.lower():
        if cleaned.lower() not in subject.lower():
            cleaned = sanitize_pin_title(f"{subject}: {cleaned}")
        elif subject:
            cleaned = subject
    return _collapse_repeated_title(cleaned)


_HOOK_WORD_LIMIT = 8
_GENERIC_HOOK = re.compile(r"^what the records leave out about\b", re.IGNORECASE)
_DANGLING_HOOK = frozenset(
    {
        "a", "an", "the", "of", "in", "on", "to", "for", "and", "or", "with",
        "from", "by", "at", "into", "over",
    }
)


def trim_dangling_hook(text: str) -> str:
    """Drop a trailing preposition so the hook does not stop mid-phrase."""
    words = (text or "").split()
    while len(words) > 1 and words[-1].lower().strip(".,;:!?\"'") in _DANGLING_HOOK:
        words.pop()
    return " ".join(words)


def short_hook(text: str, *, max_words: int = _HOOK_WORD_LIMIT) -> str:
    """At most ``max_words`` words, ending on a whole phrase."""
    from shared.pin_text import normalize_pin_text  # noqa: PLC0415

    words = normalize_pin_text(text).split()[:max_words]
    return trim_dangling_hook(" ".join(words))


def card_hook_text(record: dict) -> str:
    """Short topic hook. The AI visual_hook wins over a long generic template."""
    from shared.pin_text import normalize_pin_text  # noqa: PLC0415

    topic = normalize_pin_text(str(record.get("topic") or record.get("pinterest_title") or ""))
    title = str(record.get("pinterest_title") or topic)
    visual = normalize_pin_text(str(record.get("visual_hook") or ""))
    template = theme_string("hook_template")
    templated = normalize_pin_text(_fill(template, topic=topic, title=title)) if template else ""
    same_as_template = bool(templated) and visual.lower() == templated.lower()
    generic = bool(_GENERIC_HOOK.match(visual)) or (
        same_as_template and len(templated.split()) > _HOOK_WORD_LIMIT
    )
    if visual and not generic:
        return short_hook(visual)
    template_is_short = bool(templated) and len(templated.split()) <= _HOOK_WORD_LIMIT
    if template_is_short and not _GENERIC_HOOK.match(templated):
        return short_hook(templated)
    return short_hook(topic or title)


def card_cta_label() -> str:
    """CTA strip. ``pin_theme.cta_label`` overrides the older button label."""
    from production.formats.pins import config as cfg  # noqa: PLC0415

    return theme_string("cta_label") or (cfg.CTA_BUTTON_LABEL or "").strip()


def card_site_domain(record: dict | None = None) -> str:
    """Host printed on the card. The pin link itself can be the article URL."""
    domain = theme_string("site_domain")
    if domain:
        return domain.replace("https://", "").replace("http://", "").strip("/")
    candidates = []
    if record:
        candidates.append(str(record.get("target_url") or ""))
    from production.formats.pins import config as cfg  # noqa: PLC0415

    candidates.append(cfg.TARGET_URL or "")
    for url in candidates:
        host = (urlsplit(url if "://" in url else f"https://{url}").hostname or "").lower()
        if host:
            return host
    return ""


_DESCRIPTION_LIMIT = 220


def pin_alt_text(record: dict) -> str:
    """Alt text from the article title, then the topic. Pinterest allows 500 characters."""
    from shared.pin_text import polish_pin_text  # noqa: PLC0415

    title = polish_pin_text(str(record.get("pinterest_title") or ""))
    topic = polish_pin_text(str(record.get("topic") or ""))
    return (title or topic)[:500]


def build_pin_description(record: dict) -> str:
    """About 220 characters, ending with the CTA line and the article URL."""
    from production.formats.pins.articles import resolve_pin_link  # noqa: PLC0415
    from shared.pin_text import polish_pin_text  # noqa: PLC0415

    title = keyword_first_title(
        str(record.get("pinterest_title") or ""),
        str(record.get("topic") or ""),
    )
    topic = polish_pin_text(str(record.get("topic") or title))
    url = resolve_pin_link(record)
    lead_template = theme_string("description_lead") or "{title}."
    closer_template = theme_string("description_closer") or "Read the full article at {url}"
    lead = polish_pin_text(_fill(lead_template, title=title, topic=topic, url=url))
    closer = ""
    if url:
        closer = polish_pin_text(_fill(closer_template, title=title, topic=topic, url=url))
    if not closer:
        return _with_credit(lead[:_DESCRIPTION_LIMIT], record)
    separator = "\n"
    budget = _DESCRIPTION_LIMIT - len(closer) - len(separator)
    if budget < 1 or len(closer) >= _DESCRIPTION_LIMIT:
        return _with_credit(closer, record)
    if len(lead) > budget:
        trimmed = lead[:budget].rsplit(" ", 1)[0].rstrip(" ,;:-—")
        lead = trimmed or lead[:budget].rstrip()
    if not lead:
        body = closer
    else:
        body = f"{lead}{separator}{closer}"
    return _with_credit(body, record)


def _with_credit(body: str, record: dict) -> str:
    """Append the short attribution. A description with no credit stays within 220."""
    credit = str(record.get("image_credit") or "").strip()
    if not credit or credit in body:
        return body
    combined = f"{body}\n{credit}" if body else credit
    if len(combined) <= 500:
        return combined
    room = 500 - len(credit) - 1
    if room < 1:
        return credit[:500]
    trimmed = body[:room].rsplit(" ", 1)[0].rstrip(" ,;:-—")
    return f"{trimmed}\n{credit}" if trimmed else credit[:500]


def missing_required_credit(record: dict) -> bool:
    """True when a CC BY or CC BY-SA image has no real author and license line."""
    from production.providers.image.free.attribution import author_missing  # noqa: PLC0415

    license_name = str(record.get("image_license") or "").strip()
    required = bool(record.get("image_credit_required")) or license_name in {
        "cc_by",
        "cc_by_sa",
    }
    if not required:
        return False
    return author_missing(str(record.get("image_credit") or ""))


def trim_pin_caption(text: str, limit: int = _CAPTION_LIMIT) -> str:
    """Pin captions stay within the Pinterest 500-character description limit.

    A caption that already fits is left unchanged, including its line breaks.
    """
    raw = text or ""
    if len(raw) <= limit:
        return raw
    from shared.pin_text import fit_pin_title, polish_pin_text  # noqa: PLC0415

    return fit_pin_title(polish_pin_text(raw), limit)


def polish_stored_pin_copy(entry: dict) -> None:
    """Collapse a repeated title and trim an over-long caption on a stored pin."""
    title = _collapse_repeated_title(str(entry.get("pinterest_title") or ""))
    if title:
        entry["pinterest_title"] = title
    caption = str(entry.get("pinterest_caption") or "")
    if caption:
        entry["pinterest_caption"] = trim_pin_caption(caption)


def finalize_pin_copy(entry: dict) -> None:
    """Apply the config title and hook onto one inventory entry."""
    topic = str(entry.get("topic") or "")
    entry["pinterest_title"] = keyword_first_title(str(entry.get("pinterest_title") or ""), topic)
    caption = str(entry.get("pinterest_caption") or "")
    if caption:
        entry["pinterest_caption"] = trim_pin_caption(caption)
    hook = card_hook_text(entry)
    if hook:
        entry["visual_hook"] = hook
