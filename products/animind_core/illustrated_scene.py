"""Contract for a future illustrated plate with an animatable face overlay.

This module performs geometry and validation only.  It never generates,
downloads or edits the plate image.  A future renderer can load a scene whose
face area is intentionally blank and place the existing eye and mouth rigs at
the declared anchor.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FaceAnchor:
    """Face centre and scale, normalized to the plate dimensions."""

    x: float
    y: float
    scale: float
    facing: str = "front"

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "FaceAnchor":
        anchor = cls(
            x=float(value["x"]),
            y=float(value["y"]),
            scale=float(value["scale"]),
            facing=str(value.get("facing") or "front"),
        )
        if not 0.0 <= anchor.x <= 1.0 or not 0.0 <= anchor.y <= 1.0:
            raise ValueError("face anchor x and y must be normalized to 0..1")
        if not 0.01 <= anchor.scale <= 1.0:
            raise ValueError("face anchor scale must be between 0.01 and 1")
        if anchor.facing not in {"front", "left", "right"}:
            raise ValueError("face anchor facing must be front, left, or right")
        return anchor

    def overlay_box(
        self,
        plate_size: tuple[int, int],
        *,
        width_units: float,
        height_units: float,
        offset_units: tuple[float, float] = (0.0, 0.0),
    ) -> tuple[int, int, int, int]:
        """Pixel box for an overlay measured in face-scale units."""
        width, height = plate_size
        if width <= 0 or height <= 0:
            raise ValueError("plate dimensions must be positive")
        face_px = self.scale * min(width, height)
        cx = self.x * width + offset_units[0] * face_px
        cy = self.y * height + offset_units[1] * face_px
        overlay_w = max(1, round(width_units * face_px))
        overlay_h = max(1, round(height_units * face_px))
        return (
            round(cx - overlay_w / 2),
            round(cy - overlay_h / 2),
            round(cx + overlay_w / 2),
            round(cy + overlay_h / 2),
        )


@dataclass(frozen=True)
class IllustratedScene:
    image: str
    width: int
    height: int
    faces: tuple[FaceAnchor, ...]

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "IllustratedScene":
        image = str(value.get("image") or "").strip()
        width, height = int(value.get("width") or 0), int(value.get("height") or 0)
        faces = tuple(FaceAnchor.from_dict(face) for face in value.get("faces") or [])
        if not image:
            raise ValueError("illustrated scene image is required")
        if width <= 0 or height <= 0:
            raise ValueError("illustrated scene dimensions must be positive")
        if not faces:
            raise ValueError("illustrated scene needs at least one face anchor")
        return cls(image=image, width=width, height=height, faces=faces)


def capability() -> dict[str, Any]:
    """Public schema summary; generation deliberately remains disabled."""
    return {
        "mode": "illustrated-scene",
        "status": "contract-only",
        "generation": False,
        "plate": {"required": ["image", "width", "height", "faces"]},
        "faceAnchor": {
            "coordinates": "normalized",
            "required": ["x", "y", "scale"],
            "facing": ["front", "left", "right"],
            "overlays": ["eyes", "mouth"],
        },
    }
