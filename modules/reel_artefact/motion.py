# -*- coding: utf-8 -*-
"""Slow push-in for a real photograph. The compiler reads the sidecar."""
from __future__ import annotations

import json
from pathlib import Path

# Short moves. The aggressive 1.42 cycle would crop the artefact away.
SLOW_PUSH = {
    "zoom_start": 1.00,
    "zoom_end": 1.10,
    "pan_mul": 0.35,
    "reverse_dir": False,
    "profile": "slow_push",
}
_PROFILES = {
    "slow_push": SLOW_PUSH,
    "slow_pull": {
        "zoom_start": 1.12,
        "zoom_end": 1.02,
        "pan_mul": 0.40,
        "reverse_dir": True,
        "profile": "slow_pull",
    },
    "pan_left": {
        "zoom_start": 1.04,
        "zoom_end": 1.10,
        "pan_mul": 0.85,
        "reverse_dir": False,
        "profile": "pan_left",
    },
    "pan_right": {
        "zoom_start": 1.04,
        "zoom_end": 1.10,
        "pan_mul": 0.85,
        "reverse_dir": True,
        "profile": "pan_right",
    },
}
_CYCLE = ("slow_push", "slow_pull", "pan_left", "pan_right")


def profile_for(index: int) -> str:
    """Ken Burns move for beat ``index``. Successive beats do not repeat."""
    return _CYCLE[int(index) % len(_CYCLE)]


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
    name = str(payload.get("profile") or "")
    profile = _PROFILES.get(name)
    if profile is None:
        return None
    return dict(profile)
