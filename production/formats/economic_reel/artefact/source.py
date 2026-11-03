# -*- coding: utf-8 -*-
"""Sidecar that tells the judge which photograph a still came from."""
from __future__ import annotations

import json
from pathlib import Path


def source_sidecar_path(image_path: Path | str) -> Path:
    return Path(str(image_path) + ".source.json")


def write_source_sidecar(image_path: Path | str, payload: dict) -> Path:
    path = source_sidecar_path(image_path)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def source_meta(image_path: Path | str | None) -> dict:
    if not image_path:
        return {}
    path = source_sidecar_path(image_path)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}
