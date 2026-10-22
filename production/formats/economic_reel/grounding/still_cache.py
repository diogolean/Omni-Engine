# -*- coding: utf-8 -*-
"""Reuse a still only when it already passed QA for the same spoken line.

Files left in the shared subject-assets folder by an older episode have no
sidecar, so they are not reused. That stops an August frame of the same
topic slug from becoming this episode's first image.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def spoken_fingerprint(text: str) -> str:
    norm = " ".join((text or "").lower().split())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


def sidecar_path(image: Path | str) -> Path:
    return Path(str(image) + ".spoken.json")


def write_spoken_sidecar(
    image: Path | str,
    spoken: str,
    *,
    passed: bool,
    score: float | None = None,
) -> None:
    path = sidecar_path(image)
    payload = {
        "spoken_sha": spoken_fingerprint(spoken),
        "spoken": " ".join((spoken or "").split())[:400],
        "passed": bool(passed),
        "score": score,
    }
    try:
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError:
        return


def cache_matches_spoken(image: Path | str, spoken: str) -> bool:
    path = sidecar_path(image)
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict) or data.get("passed") is not True:
        return False
    return data.get("spoken_sha") == spoken_fingerprint(spoken)
