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


def keyword_first_title(title: str, topic: str) -> str:
    """Search title, subject first, at most 100 characters. No template suffix.

    The topic is not prepended when the title already names it. A title that
    would pass 100 characters ends on a word boundary.
    """
    from shared.pin_text import sanitize_pin_title, title_case_phrase  # noqa: PLC0415

    cleaned = sanitize_pin_title(title)
    subject = sanitize_pin_title(title_case_phrase(topic))
    if not cleaned:
        return subject
    if _topic_already_named(cleaned, subject):
        return cleaned
    head = ""
    for word in re.split(r"[^A-Za-z0-9']+", subject.lower()):
        if len(word) > 2 and word not in _TITLE_STOPWORDS:
            head = word
            break
    if head and head not in cleaned.lower():
        cleaned = sanitize_pin_title(f"{subject}: {cleaned}")
    return cleaned


_HOOK_WORD_LIMIT = 8
_GENERIC_HOOK = re.compile(r"^what the records leave out about\b", re.IGNORECASE)


def short_hook(text: str, *, max_words: int = _HOOK_WORD_LIMIT) -> str:
    """At most ``max_words`` words. The card renderer also caps this at 3 lines."""
    from shared.pin_text import normalize_pin_text  # noqa: PLC0415

    words = normalize_pin_text(text).split()
    return " ".join(words[:max_words])


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
        return lead[:_DESCRIPTION_LIMIT]
    separator = "\n"
    budget = _DESCRIPTION_LIMIT - len(closer) - len(separator)
    if budget < 1 or len(closer) >= _DESCRIPTION_LIMIT:
        return closer
    if len(lead) > budget:
        trimmed = lead[:budget].rsplit(" ", 1)[0].rstrip(" ,;:-—")
        lead = trimmed or lead[:budget].rstrip()
    if not lead:
        return closer
    return f"{lead}{separator}{closer}"


def finalize_pin_copy(entry: dict) -> None:
    """Apply the config title and hook onto one inventory entry."""
    topic = str(entry.get("topic") or "")
    entry["pinterest_title"] = keyword_first_title(str(entry.get("pinterest_title") or ""), topic)
    hook = card_hook_text(entry)
    if hook:
        entry["visual_hook"] = hook
