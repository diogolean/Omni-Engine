# -*- coding: utf-8 -*-
"""Query cache so a repeated topic does not hit the provider APIs again."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from production.providers.image.free.models import Candidate

_CACHE_VERSION = 1


def cache_key(query: str, providers: list[str]) -> str:
    payload = json.dumps(
        {"v": _CACHE_VERSION, "q": query.strip().lower(), "p": sorted(providers)},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


class QueryCache:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def read(self, key: str) -> list[Candidate] | None:
        path = self._path(key)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict) or payload.get("v") != _CACHE_VERSION:
            return None
        rows = payload.get("candidates")
        if not isinstance(rows, list):
            return None
        return [Candidate.from_json(row) for row in rows if isinstance(row, dict)]

    def write(self, key: str, query: str, candidates: list[Candidate]) -> None:
        path = self._path(key)
        payload = {
            "v": _CACHE_VERSION,
            "query": query,
            "candidates": [candidate.to_json() for candidate in candidates],
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
