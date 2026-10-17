# -*- coding: utf-8 -*-
"""Value types for a free-license image search."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Candidate:
    """Metadata for one remote image. Bytes are downloaded later, one file at a time."""

    provider: str
    title: str = ""
    description: str = ""
    author: str = ""
    license_raw: str = ""
    license_url: str = ""
    source_url: str = ""
    download_url: str = ""
    thumbnail_url: str = ""
    width: int = 0
    height: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> Candidate:
        known = {item.name for item in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        data = {key: value for key, value in payload.items() if key in known}
        data["extra"] = dict(data.get("extra") or {})
        data["width"] = int(data.get("width") or 0)
        data["height"] = int(data.get("height") or 0)
        return cls(**data)


@dataclass
class Attribution:
    title: str
    author: str
    license: str
    source_url: str
    license_url: str = ""
    provider: str = ""

    def to_json(self) -> dict[str, str]:
        return asdict(self)


@dataclass
class FreeImageHit:
    """A vetted source file. ``raw_publish_allowed`` stays false."""

    path: Path
    sidecar_path: Path
    attribution: Attribution
    credit_line: str
    width: int
    height: int
    score: float
    provider: str
    smart_cropped: bool = False
    raw_publish_allowed: bool = False

    def as_publishable(self) -> None:
        raise RuntimeError(
            "Free-license images are template inputs. Composite a card, hook, "
            "or reel treatment before publishing."
        )


@dataclass
class SourceDecision:
    policy: str
    query: str
    reason: str
    hit: FreeImageHit | None = None
    used_fallback: bool = False
    fallback_path: Path | None = None
