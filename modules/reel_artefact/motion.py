# -*- coding: utf-8 -*-
"""Slow push-in for a real photograph. The compiler reads the sidecar."""
from __future__ import annotations

import json
from pathlib import Path

# A short push. The aggressive 1.42 cycle would crop the artefact away.
SLOW_PUSH = {
    "zoom_start": 1.00,
    "zoom_end": 1.10,
    "pan_mul": 0.35,
    "reverse_dir": False,
    "profile": "slow_push",
}


def motion_sidecar_path(image_path: Path | str) -> Path:
    return Path(str(image_path) + ".motion.json")


def write_motion_sidecar(image_path: Path | str, profile: str = "slow_push") -> Path:
    path = motion_sidecar_path(image_path)
    path.write_text(
        json.dumps({"profile": profile}, indent=2),
        encoding="utf-8",
    )
    return path


def motion_override(image_path: Path | str) -> dict | None:
    """Profile dict for the compiler, or None to keep the act cycle."""
    path = motion_sidecar_path(image_path)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if str(payload.get("profile") or "") != "slow_push":
        return None
    return dict(SLOW_PUSH)
