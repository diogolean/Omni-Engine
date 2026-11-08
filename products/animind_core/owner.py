"""Opaque owner of a job and the files it points at.

Same rule as the persona sheet: ``X-Animind-User`` is an opaque id, stored
only as a hash. An email is refused. A request without that header is
anonymous and cannot read someone else's job.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from . import settings

_USER_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
FILE_SUFFIXES = {".png", ".json", ".wav", ".mp4", ".mp3"}


def accept_user(header: str | None) -> str | None:
    """Return the owner hash, or None when the caller is anonymous."""
    raw = (header or "").strip()
    if not raw:
        return None
    if "@" in raw or not _USER_ID.match(raw):
        raise ValueError("user")
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def job_rel(owner: str | None, job_id: str) -> str:
    """Relative path under ``ANIMIND_DATA_DIR`` for one render."""
    if owner:
        return f"users/{owner}/jobs/{job_id}"
    return f"jobs/{job_id}"


def readable_file(path: str, owner: str | None) -> Path | None:
    """A file the caller may read, or None. Owned trees require the same hash."""
    root = settings.data_dir().resolve()
    for sub in ("assets", ""):
        target = (root / sub / path).resolve()
        if _allowed(target, root, owner):
            return target
    return None


def _allowed(target: Path, root: Path, owner: str | None) -> bool:
    if not target.is_relative_to(root) or not target.is_file() or target.suffix not in FILE_SUFFIXES:
        return False
    rel = target.relative_to(root)
    if not rel.parts or rel.parts[0] != "users":
        # Loose render output is unreachable. Puppets and previews stay public.
        if rel.parts and rel.parts[0] == "jobs":
            return False
        return True
    return owner is not None and len(rel.parts) > 1 and rel.parts[1] == owner
