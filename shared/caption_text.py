# -*- coding: utf-8 -*-
"""Turn LLM caption payloads into plain text. No production or distribution imports."""
from __future__ import annotations

import json
import re
from typing import Any

_CAPTION_JSON_KEYS: tuple[str, ...] = (
    "caption_body",
    "caption",
    "description",
    "text",
    "post_caption",
)
_FENCE_BLOCK_RE = re.compile(
    r"^```(?:json|JSON)?\s*\n?(.*?)\n?```\s*$",
    re.DOTALL,
)
_FENCE_START_RE = re.compile(r"^```(?:json|JSON)?\s*\n?", re.IGNORECASE)
_FENCE_END_RE = re.compile(r"\n?```\s*$")
_JSON_KEY_ARTIFACT_RE = re.compile(
    r'"?\b(caption_body|caption|description|image_text_overlay|visual_subject|'
    r'quote_text|seo_title|text|post_caption)\b"?\s*:\s*',
    re.IGNORECASE,
)


def extract_clean_caption(raw: Any) -> str:
    """Extract plain post-caption text from LLM output.

    Handles dict payloads, markdown-fenced JSON, serialized JSON objects,
    and already-clean plain text.
    """
    if raw is None:
        return ""

    if isinstance(raw, dict):
        for key in _CAPTION_JSON_KEYS:
            val = raw.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        return ""

    if isinstance(raw, (list, tuple)):
        return " ".join(str(v) for v in raw).strip()

    text = str(raw).strip()
    if not text:
        return ""

    fence = _FENCE_BLOCK_RE.match(text)
    if fence:
        text = fence.group(1).strip()
    elif text.startswith("```"):
        text = _FENCE_START_RE.sub("", text)
        text = _FENCE_END_RE.sub("", text).strip()

    if text[:1] in "{[":
        try:
            parsed = json.loads(text)
            extracted = extract_clean_caption(parsed)
            if extracted:
                return extracted
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

        for key in _CAPTION_JSON_KEYS:
            for pat in (
                rf'"{key}"\s*:\s*"(.*?)"\s*,?\s*\n\s*"',
                rf'"{key}"\s*:\s*"(.*?)"\s*\n?\s*[}}\]]',
            ):
                match = re.search(pat, text, re.DOTALL)
                if match:
                    val = match.group(1)
                    val = (
                        val.replace("\\n", "\n")
                        .replace("\\t", "\t")
                        .replace('\\"', '"')
                        .replace("\\\\", "\\")
                    )
                    return val.strip()

        text = text.strip("{}[]").strip()
        text = _JSON_KEY_ARTIFACT_RE.sub("", text).strip()

    return text
