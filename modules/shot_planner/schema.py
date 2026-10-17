# -*- coding: utf-8 -*-
"""Shot-list schema. Stdlib only, so CI and the ARM image can import it."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SHOT_TYPES = ("macro", "close", "medium", "wide", "aerial", "over_shoulder")
_SHOT_FIELDS = (
    "id",
    "narration",
    "entity_id",
    "subject",
    "canonical_visual",
    "shot_type",
    "camera",
    "setting",
    "era",
    "mood",
    "lighting",
    "negatives",
)


@dataclass
class Shot:
    id: str
    narration: str
    entity_id: str
    subject: str
    canonical_visual: str
    shot_type: str
    camera: str
    setting: str
    era: str
    mood: str
    lighting: str
    negatives: list[str] = field(default_factory=list)
    kb_miss: bool = False
    canonical_shape: str = ""
    image_role: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "narration": self.narration,
            "entity_id": self.entity_id,
            "subject": self.subject,
            "canonical_visual": self.canonical_visual,
            "shot_type": self.shot_type,
            "camera": self.camera,
            "setting": self.setting,
            "era": self.era,
            "mood": self.mood,
            "lighting": self.lighting,
            "negatives": list(self.negatives),
            "kb_miss": self.kb_miss,
            "canonical_shape": self.canonical_shape,
            "image_role": self.image_role,
        }


@dataclass
class ShotList:
    channel: str
    topic: str
    narration: str
    shots: list[Shot]
    angle: str = ""
    source: dict[str, str] = field(
        default_factory=lambda: {"kind": "topic", "ref": "", "title": ""},
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "topic": self.topic,
            "angle": self.angle,
            "source": dict(self.source),
            "narration": self.narration,
            "shots": [shot.to_dict() for shot in self.shots],
        }


def _as_str(value: Any) -> str:
    return " ".join(str(value or "").split())


def shot_from_dict(raw: dict[str, Any]) -> Shot:
    negatives = raw.get("negatives") or []
    if not isinstance(negatives, list):
        negatives = []
    return Shot(
        id=_as_str(raw.get("id")),
        narration=_as_str(raw.get("narration")),
        entity_id=_as_str(raw.get("entity_id")),
        subject=_as_str(raw.get("subject")),
        canonical_visual=_as_str(raw.get("canonical_visual")),
        shot_type=_as_str(raw.get("shot_type")).lower(),
        camera=_as_str(raw.get("camera")),
        setting=_as_str(raw.get("setting")),
        era=_as_str(raw.get("era")),
        mood=_as_str(raw.get("mood")),
        lighting=_as_str(raw.get("lighting")),
        negatives=[_as_str(item) for item in negatives if _as_str(item)],
        kb_miss=bool(raw.get("kb_miss")),
        canonical_shape=_as_str(raw.get("canonical_shape")),
        image_role=_as_str(raw.get("image_role")).lower(),
    )


def shot_list_from_dict(raw: dict[str, Any]) -> ShotList:
    rows = raw.get("shots") or []
    shots = [shot_from_dict(row) for row in rows if isinstance(row, dict)]
    source = raw.get("source") if isinstance(raw.get("source"), dict) else {}
    return ShotList(
        channel=_as_str(raw.get("channel")) or "ancient_knowledge",
        topic=_as_str(raw.get("topic")),
        narration=_as_str(raw.get("narration")),
        shots=shots,
        angle=_as_str(raw.get("angle")),
        source={
            "kind": _as_str(source.get("kind")) or "topic",
            "ref": _as_str(source.get("ref")),
            "title": _as_str(source.get("title")),
        },
    )


def validate_shot_list(raw: dict[str, Any]) -> list[str]:
    """Return human-readable errors. An empty list means the document is valid."""
    errors: list[str] = []
    if not isinstance(raw, dict):
        return ["shot list must be a JSON object"]
    if not _as_str(raw.get("channel")):
        errors.append("channel is required")
    if not _as_str(raw.get("topic")):
        errors.append("topic is required")
    shots = raw.get("shots")
    if not isinstance(shots, list) or not shots:
        errors.append("shots must be a non-empty list")
        return errors
    for index, row in enumerate(shots, start=1):
        if not isinstance(row, dict):
            errors.append(f"shot {index} must be an object")
            continue
        for key in _SHOT_FIELDS:
            if key == "negatives":
                continue
            if not _as_str(row.get(key)):
                errors.append(f"shot {index} missing {key}")
        if _as_str(row.get("shot_type")).lower() not in SHOT_TYPES:
            errors.append(f"shot {index} shot_type must be one of {', '.join(SHOT_TYPES)}")
        negatives = row.get("negatives")
        if not isinstance(negatives, list) or any(not _as_str(item) for item in negatives):
            errors.append(f"shot {index} negatives must be a list of strings")
    return errors
