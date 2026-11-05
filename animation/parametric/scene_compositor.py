"""Stream one RGB frame at a time from a scene spec.

The puppet is posed, the mouth follows the analyzed viseme track, and a
single camera places the result. Frames are not collected.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Iterator

import cv2
import numpy as np
from PIL import Image

from .animator_types import REST_VISEME, AnalyzedAudio
from .audio_analyzer import breathing_offset
from .puppet import PuppetRig
from .scene_spec import (
    CameraKey,
    CharacterSpec,
    LineSpec,
    SceneSpec,
    sample_linear,
    sample_step,
)

_FIT = {"full": 0.88, "medium": 0.96, "closeup": 1.08}


class SceneCompositor:
    """Yield ``width x height`` RGB frames for one analyzed take."""

    def __init__(
        self,
        *,
        spec: SceneSpec,
        rigs: dict[str, PuppetRig],
        lines: tuple[LineSpec, ...],
        width: int,
        height: int,
    ) -> None:
        if width < 2 or height < 2:
            raise ValueError("the frame must be at least 2 pixels on each side")
        missing = [character.id for character in spec.characters if character.id not in rigs]
        if missing:
            raise ValueError(f"missing rigs for {', '.join(missing)}")
        self.spec = spec
        self.rigs = rigs
        self.lines = lines
        self.width = int(width)
        self.height = int(height)
        self._by_id = {character.id: character for character in spec.characters}
        self._pose = {
            character.id: _pose_tracks(character) for character in spec.characters
        }
        self._expression = {
            character.id: tuple((key.t, key.emotion) for key in character.expression)
            for character in spec.characters
        }
        camera = spec.camera or (CameraKey(t=0.0),)
        self._camera_mode = tuple((key.t, key.mode) for key in camera)
        self._camera_zoom = tuple((key.t, key.zoom) for key in camera)
        self._camera_pan_x = tuple((key.t, key.pan_x) for key in camera)
        self._camera_pan_y = tuple((key.t, key.pan_y) for key in camera)
        self._background = np.empty((self.height, self.width, 3), dtype=np.uint8)
        self._background[:, :] = np.asarray(spec.background, dtype=np.uint8)
        if spec.background_image:
            self._background = _load_background(
                spec.background_image,
                spec.base_dir,
                self.width,
                self.height,
            )

    def iter_frames(self, analyzed: AnalyzedAudio) -> Iterator[np.ndarray]:
        """One frame per pull. The sequence is never stored."""
        for index in range(analyzed.n_frames):
            yield self.frame(index, analyzed)

    def frame(self, index: int, analyzed: AnalyzedAudio) -> np.ndarray:
        t = index / float(analyzed.fps)
        canvas = self._background.copy()
        visible = self._visible(t)
        mode = str(sample_step(self._camera_mode, t, "medium"))
        zoom = float(sample_linear(self._camera_zoom, t, 1.0))
        pan_x = float(sample_linear(self._camera_pan_x, t, 0.0))
        pan_y = float(sample_linear(self._camera_pan_y, t, 0.0))
        for slot, character in enumerate(visible):
            sprite = self._sprite(character, index, t, analyzed)
            _place(
                canvas,
                sprite,
                mode=mode,
                zoom=zoom,
                pan_x=pan_x,
                pan_y=pan_y,
                slot_index=slot,
                slot_count=len(visible),
            )
        if canvas.shape != (self.height, self.width, 3):
            raise RuntimeError("scene frame has the wrong shape")
        return canvas

    def _visible(self, t: float) -> list[CharacterSpec]:
        if self.spec.stage == "duo":
            return list(self.spec.characters)
        if self.spec.stage == "solo":
            return [self._by_id[self.spec.focus_id]]
        return [self._by_id[self._speaker_at(t)]]

    def _speaker_at(self, t: float) -> str:
        fallback = self.spec.focus_id
        for line in self.lines:
            end = math.inf if line.end is None else line.end
            if line.start <= t < end:
                return line.speaker
            if line.start <= t:
                fallback = line.speaker
        return fallback

    def _sprite(
        self,
        character: CharacterSpec,
        index: int,
        t: float,
        analyzed: AnalyzedAudio,
    ) -> np.ndarray:
        rig = self.rigs[character.id]
        tracks = self._pose[character.id]
        if tracks["head_angle"]:
            head_angle = sample_linear(tracks["head_angle"], t, 0.0)
        else:
            head_angle = _idle_head(t)
        if tracks["y_offset"]:
            y_offset = sample_linear(tracks["y_offset"], t, 0.0)
        else:
            y_offset = breathing_offset(t)
        scale = sample_linear(tracks["scale"], t, 1.0)
        glow = sample_linear(tracks["glow"], t, 0.0)
        brightness = sample_linear(tracks["brightness"], t, 1.0)
        emotion = str(sample_step(self._expression[character.id], t, ""))
        if not emotion:
            emotion = str(_at(analyzed.emotion, index, "neutral"))
        viseme = _at(analyzed.viseme.get(character.id, ()), index, REST_VISEME)
        eye_state = int(_at(analyzed.eye_state.get(character.id, ()), index, 0))
        rgba = rig.compose(
            viseme=str(viseme),
            eye_state=eye_state,
            y_offset=float(y_offset),
            scale=float(scale),
            brightness=float(brightness),
            glow_intensity=float(glow),
            head_angle=float(head_angle),
            emotion=emotion,
        )
        mode = str(sample_step(self._camera_mode, t, "medium"))
        cropped = _crop(mode, rgba, rig)
        if character.facing == "left":
            cropped = cv2.flip(cropped, 1)
        return np.ascontiguousarray(cropped)


def _pose_tracks(character: CharacterSpec) -> dict[str, tuple[tuple[float, float], ...]]:
    tracks: dict[str, tuple[tuple[float, float], ...]] = {}
    for field in ("head_angle", "y_offset", "scale", "glow", "brightness"):
        points = [
            (key.t, float(getattr(key, field)))
            for key in character.pose
            if getattr(key, field) is not None
        ]
        tracks[field] = tuple(points)
    return tracks


def _idle_head(t: float) -> float:
    return math.sin(t * 1.1) * 2.0


def _crop(mode: str, rgba: np.ndarray, rig: PuppetRig) -> np.ndarray:
    height, width = rgba.shape[:2]
    if mode == "full":
        return rgba
    x0, y0, x1, y1 = rig.closeup_box()
    x0, y0 = max(0, int(x0)), max(0, int(y0))
    x1, y1 = min(width, int(x1)), min(height, int(y1))
    if mode == "closeup":
        box_h = max(2, y1 - y0)
        box_w = max(2, x1 - x0)
        y1 = min(height, y0 + max(2, int(box_h * 0.62)))
        center = (x0 + x1) // 2
        half = max(2, int(box_w * 0.38))
        x0, x1 = max(0, center - half), min(width, center + half)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return rgba
    return rgba[y0:y1, x0:x1]


def _place(
    canvas: np.ndarray,
    sprite: np.ndarray,
    *,
    mode: str,
    zoom: float,
    pan_x: float,
    pan_y: float,
    slot_index: int,
    slot_count: int,
) -> None:
    frame_h, frame_w = canvas.shape[:2]
    sprite_h, sprite_w = sprite.shape[:2]
    if sprite_h < 1 or sprite_w < 1:
        return
    fraction = _FIT.get(mode, 0.96)
    target_h = max(2, int(round(frame_h * fraction * zoom)))
    if slot_count > 1:
        target_h = max(2, int(target_h * 0.92))
    target_w = max(2, int(round(sprite_w * (target_h / float(sprite_h)))))
    resized = cv2.resize(sprite, (target_w, target_h), interpolation=cv2.INTER_LINEAR)
    slot_w = frame_w / float(slot_count)
    center_x = (slot_index + 0.5) * slot_w + pan_x * frame_w
    center_y = frame_h * 0.52 + pan_y * frame_h
    x = int(round(center_x - target_w / 2.0))
    y = int(round(center_y - target_h / 2.0))
    _paste_rgba(canvas, resized, x, y)


def _paste_rgba(dest: np.ndarray, src_rgba: np.ndarray, x: int, y: int) -> None:
    """Alpha-composite one sprite onto an RGB frame, clipped to the frame."""
    frame_h, frame_w = dest.shape[:2]
    sprite_h, sprite_w = src_rgba.shape[:2]
    dst_x0, dst_y0 = max(0, x), max(0, y)
    dst_x1, dst_y1 = min(frame_w, x + sprite_w), min(frame_h, y + sprite_h)
    if dst_x1 <= dst_x0 or dst_y1 <= dst_y0:
        return
    src_x0, src_y0 = dst_x0 - x, dst_y0 - y
    src_x1 = src_x0 + (dst_x1 - dst_x0)
    src_y1 = src_y0 + (dst_y1 - dst_y0)
    src = np.ascontiguousarray(src_rgba[src_y0:src_y1, src_x0:src_x1])
    region = dest[dst_y0:dst_y1, dst_x0:dst_x1]
    alpha = src[..., 3]
    inv_alpha = cv2.bitwise_not(alpha)
    alpha_3 = cv2.merge([alpha, alpha, alpha])
    inv_alpha_3 = cv2.merge([inv_alpha, inv_alpha, inv_alpha])
    fg = cv2.multiply(src[..., :3], alpha_3, scale=1.0 / 255.0)
    bg = cv2.multiply(region, inv_alpha_3, scale=1.0 / 255.0)
    dest[dst_y0:dst_y1, dst_x0:dst_x1] = cv2.add(fg, bg)


def _load_background(image: str, base_dir: Path, width: int, height: int) -> np.ndarray:
    path = Path(image)
    if not path.is_absolute():
        path = base_dir / path
    if not path.is_file():
        raise FileNotFoundError(f"background image not found: {path}")
    with Image.open(path) as handle:
        rgb = np.asarray(handle.convert("RGB"), dtype=np.uint8)
    if rgb.shape[0] != height or rgb.shape[1] != width:
        rgb = cv2.resize(rgb, (width, height), interpolation=cv2.INTER_AREA)
    return np.ascontiguousarray(rgb)


def _at(seq, index: int, default):
    if not seq or index >= len(seq) or index < 0:
        return default
    return seq[index]


__all__ = ["SceneCompositor"]
