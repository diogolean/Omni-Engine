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
    from agents.posting.pinterest_engine import config as cfg  # noqa: PLC0415

    raw = (getattr(cfg, "PIN_THEME", None) or {}).get(name)
    if isinstance(raw, str):
        return raw.strip()
    return ""


def _fill(template: str, **values: str) -> str:
    class _Blank(dict):
        def __missing__(self, key: str) -> str:
            return ""

    return template.format_map(_Blank({key: value or "" for key, value in values.items()})).strip()


def keyword_first_title(title: str, topic: str) -> str:
    """Search title, subject first, at most 100 characters. No template suffix."""
    from agents.posting.pinterest_engine.inventory import (  # noqa: PLC0415
        sanitize_pin_title,
        title_case_phrase,
    )

    cleaned = sanitize_pin_title(title)
    subject = sanitize_pin_title(title_case_phrase(topic))
    if not cleaned:
        return subject[:100]
    head = ""
    for word in re.split(r"[^A-Za-z0-9']+", subject.lower()):
        if len(word) > 2:
            head = word
            break
    opening = cleaned.lower()[: max(24, len(head) + 8)]
    if head and head not in opening:
        cleaned = sanitize_pin_title(f"{subject}: {cleaned}")
    return cleaned[:100]


def card_hook_text(record: dict) -> str:
    """Curiosity-gap headline for the hook panel. The template is the tunable part."""
    from agents.posting.pinterest_engine.inventory import normalize_pin_text  # noqa: PLC0415

    topic = normalize_pin_text(str(record.get("topic") or record.get("pinterest_title") or ""))
    template = theme_string("hook_template")
    if template:
        filled = _fill(template, topic=topic, title=str(record.get("pinterest_title") or topic))
        if filled:
            return normalize_pin_text(filled)
    return normalize_pin_text(str(record.get("visual_hook") or topic))


def card_cta_label() -> str:
    """CTA strip. ``pin_theme.cta_label`` overrides the older button label."""
    from agents.posting.pinterest_engine import config as cfg  # noqa: PLC0415

    return theme_string("cta_label") or (cfg.CTA_BUTTON_LABEL or "").strip()


def card_site_domain(record: dict | None = None) -> str:
    """Host printed on the card. The pin link itself can be the article URL."""
    domain = theme_string("site_domain")
    if domain:
        return domain.replace("https://", "").replace("http://", "").strip("/")
    candidates = []
    if record:
        candidates.append(str(record.get("target_url") or ""))
    from agents.posting.pinterest_engine import config as cfg  # noqa: PLC0415

    candidates.append(cfg.TARGET_URL or "")
    for url in candidates:
        host = (urlsplit(url if "://" in url else f"https://{url}").hostname or "").lower()
        if host:
            return host
    return ""


def build_pin_description(record: dict) -> str:
    """Keyword-first sentence, then the configured line that names the article URL."""
    from agents.posting.pinterest_engine import config as cfg  # noqa: PLC0415
    from agents.posting.pinterest_engine.inventory import polish_pin_text  # noqa: PLC0415

    title = keyword_first_title(
        str(record.get("pinterest_title") or ""),
        str(record.get("topic") or ""),
    )
    topic = polish_pin_text(str(record.get("topic") or title))
    url = str(record.get("target_url") or cfg.TARGET_URL or "").strip()
    lead_template = theme_string("description_lead") or "{title}."
    closer_template = theme_string("description_closer") or "Read the full article at {url}"
    lead = polish_pin_text(_fill(lead_template, title=title, topic=topic, url=url))
    closer = ""
    if url:
        closer = polish_pin_text(_fill(closer_template, title=title, topic=topic, url=url))
    parts = [part for part in (lead, closer) if part]
    body = "\n\n".join(parts)
    tags = (cfg.HASHTAGS or "").strip()
    if tags:
        room = 500 - len(tags) - 2
        if len(body) > room:
            body = body[: max(0, room - 3)].rsplit(" ", 1)[0] + "..."
        return f"{body}\n\n{tags}"[:500]
    return body[:500]


def finalize_pin_copy(entry: dict) -> None:
    """Apply the config title and hook onto one inventory entry."""
    topic = str(entry.get("topic") or "")
    entry["pinterest_title"] = keyword_first_title(str(entry.get("pinterest_title") or ""), topic)
    hook = card_hook_text(entry)
    if hook:
        entry["visual_hook"] = hook
