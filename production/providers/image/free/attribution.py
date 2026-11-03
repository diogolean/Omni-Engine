# -*- coding: utf-8 -*-
"""Attribution sidecar and the credit line captions and articles can reuse."""

from __future__ import annotations

import json
from pathlib import Path

from production.providers.image.free.licenses import display_license
from production.providers.image.free.models import Attribution

# stem -> credit, plus resolved path -> credit. Process-local so a later
# card composite (``stem_final.png``) can still find the line.
_CREDITS: dict[str, str] = {}


_CREDIT_REQUIRED = frozenset({"cc_by", "cc_by_sa"})
_UNKNOWN_AUTHOR = frozenset({"", "unknown", "unknown author", "anonymous"})
_CREDIT_LIMIT = 80


def credit_required(license_name: str) -> bool:
    return (license_name or "").strip() in _CREDIT_REQUIRED


def author_missing(value: str) -> bool:
    """True when the credit names no real person or organization."""
    text = (value or "").strip()
    if not text:
        return True
    from production.providers.image.free.providers import clean_credit_name

    head = clean_credit_name(text.split(",")[0], limit=80).lower()
    return head in _UNKNOWN_AUTHOR


def credit_line(
    *,
    title: str,
    author: str,
    license_name: str,
    source_url: str = "",
) -> str:
    """Author and license short name. The work title and URL stay in the sidecar.

    HTML and extra languages are not copied into the line. A missing CC BY
    author becomes the single placeholder ``Unknown author``, which pin
    posting treats as a missing credit and refuses.
    """
    del title, source_url
    from production.providers.image.free.providers import clean_credit_name

    shown_author = clean_credit_name(author, limit=60)
    shown_license = display_license(license_name) if license_name in {
        "public_domain", "cc0", "cc_by", "cc_by_sa", "pexels", "unsplash",
    } else ((license_name or "").strip() or "see source")
    if shown_author.lower() in _UNKNOWN_AUTHOR:
        # Reels still name the placeholder once. Pin posting treats it as missing.
        line = f"Unknown author, {shown_license}" if credit_required(license_name) else shown_license
    else:
        line = f"{shown_author}, {shown_license}"
    if len(line) > _CREDIT_LIMIT:
        line = line[:_CREDIT_LIMIT].rsplit(" ", 1)[0].rstrip(" ,;")
    return line


def sidecar_path_for(image_path: Path) -> Path:
    return image_path.with_name(image_path.name + ".attribution.json")


def write_sidecar(
    image_path: Path,
    attribution: Attribution,
    *,
    credit: str,
    extra: dict | None = None,
) -> Path:
    path = sidecar_path_for(image_path)
    payload = attribution.to_json()
    payload["credit_line"] = credit
    payload["raw_publish_allowed"] = False
    payload["transform"] = "template_required"
    if extra:
        payload.update(extra)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    remember_credit(image_path, credit)
    return path


def remember_credit(image_path: Path, credit: str) -> None:
    text = (credit or "").strip()
    if not text:
        return
    path = Path(image_path)
    _CREDITS[path.stem] = text
    try:
        _CREDITS[str(path.resolve())] = text
    except OSError:
        _CREDITS[str(path)] = text


def credit_for_image(image_path: Path | str | None) -> str:
    if not image_path:
        return ""
    path = Path(image_path)
    direct = _CREDITS.get(str(path))
    if direct:
        return direct
    try:
        resolved = _CREDITS.get(str(path.resolve()))
    except OSError:
        resolved = None
    if resolved:
        return resolved
    sidecar = sidecar_path_for(path)
    loaded = _read_credit(sidecar)
    if loaded:
        return loaded
    stem = path.stem
    for key, line in _CREDITS.items():
        if "/" in key or "\\" in key:
            continue
        if stem == key or stem.startswith(key + "_"):
            return line
    if path.parent.is_dir():
        for candidate in path.parent.glob("*.attribution.json"):
            source_stem = candidate.name[: -len(".attribution.json")]
            source_stem = Path(source_stem).stem
            if stem == source_stem or stem.startswith(source_stem + "_"):
                loaded = _read_credit(candidate)
                if loaded:
                    return loaded
    return ""


def credits_for_images(paths: list | tuple) -> str:
    lines: list[str] = []
    for path in paths:
        line = credit_for_image(path)
        if line and line not in lines:
            lines.append(line)
    return "\n".join(lines)


def append_credit(caption: str, credit: str, *, limit: int = 480) -> str:
    """Add the credit line without dropping it when the caption is long."""
    text = (credit or "").strip()
    base = (caption or "").rstrip()
    if not text or text in base:
        return base
    combined = f"{base}\n\n{text}" if base else text
    if len(combined) <= limit:
        return combined
    room = limit - len(text) - 2
    if room < 40:
        return text[:limit]
    return f"{base[:room].rstrip()}\n\n{text}"


def _read_credit(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("credit_line") or "").strip()
