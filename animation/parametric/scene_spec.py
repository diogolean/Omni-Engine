"""Declarative scene spec for the parametric engine.

JSON and YAML describe the frame, the characters, and the curves. The
renderer does not know which channel asked for the picture.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

SCENE_VERSION = 1
MIN_DIMENSION = 16
MAX_DIMENSION = 1920
STAGES = ("solo", "shot", "duo")
LIP_SYNC_MODES = ("envelope", "off")
CAMERA_MODES = ("full", "medium", "closeup")
FACINGS = ("left", "right")
MAX_CHARACTERS = 4

_TOP_FIELDS = {
    "version",
    "width",
    "height",
    "fps",
    "seed",
    "stage",
    "focus",
    "lip_sync",
    "background",
    "characters",
    "lines",
    "camera",
}
_CHARACTER_FIELDS = {"id", "label", "accent", "facing", "puppet", "pose", "expression"}
_POSE_FIELDS = {"t", "head_angle", "y_offset", "scale", "glow", "brightness"}
_EXPRESSION_FIELDS = {"t", "emotion"}
_CAMERA_FIELDS = {"t", "mode", "zoom", "pan_x", "pan_y"}
_LINE_FIELDS = {"speaker", "start", "end", "text", "emotion"}
_BACKGROUND_FIELDS = {"color", "image"}


@dataclass(frozen=True, slots=True)
class PoseKey:
    """One pose sample. Omitted controls are left unset and interpolated apart."""

    t: float
    head_angle: float | None = None
    y_offset: float | None = None
    scale: float | None = None
    glow: float | None = None
    brightness: float | None = None


@dataclass(frozen=True, slots=True)
class ExpressionKey:
    t: float
    emotion: str


@dataclass(frozen=True, slots=True)
class CameraKey:
    """A full camera state. Later keys keep any control they do not set."""

    t: float
    mode: str = "medium"
    zoom: float = 1.0
    pan_x: float = 0.0
    pan_y: float = 0.0


@dataclass(frozen=True, slots=True)
class LineSpec:
    """One spoken span. ``end is None`` runs through the end of the audio."""

    speaker: str
    start: float
    end: float | None = None
    text: str = ""
    emotion: str = "neutral"


@dataclass(frozen=True, slots=True)
class CharacterSpec:
    id: str
    label: str
    accent: str = "#8AA0B0"
    facing: str = "right"
    puppet: str | None = None
    pose: tuple[PoseKey, ...] = ()
    expression: tuple[ExpressionKey, ...] = ()


@dataclass(frozen=True, slots=True)
class SceneSpec:
    """One render. ``base_dir`` resolves puppet and background paths."""

    version: int
    width: int
    height: int
    fps: int
    characters: tuple[CharacterSpec, ...]
    seed: int = 0
    stage: str = "solo"
    focus: str | None = None
    lip_sync: str = "envelope"
    background: tuple[int, int, int] = (18, 22, 28)
    background_image: str | None = None
    lines: tuple[LineSpec, ...] = ()
    camera: tuple[CameraKey, ...] = ()
    base_dir: Path = Path(".")

    @property
    def focus_id(self) -> str:
        return self.focus or self.characters[0].id


def preview_geometry(width: int, height: int, fps: int) -> tuple[int, int, int]:
    """Half resolution, capped at 12 fps. Both sides stay even and at least 16."""
    preview_w = max(MIN_DIMENSION, (int(width) // 2) // 2 * 2)
    preview_h = max(MIN_DIMENSION, (int(height) // 2) // 2 * 2)
    preview_fps = max(1, min(int(fps), 12))
    return preview_w, preview_h, preview_fps


def sample_step(points: Sequence[tuple[float, Any]], t: float, default: Any) -> Any:
    """Hold the latest sample at or before ``t``. Before the first, hold that sample."""
    if not points:
        return default
    current = points[0][1]
    for stamp, value in points:
        if stamp <= t + 1e-9:
            current = value
        else:
            break
    return current


def sample_linear(points: Sequence[tuple[float, float]], t: float, default: float) -> float:
    """Interpolate numeric samples. Ends hold."""
    if not points:
        return float(default)
    if len(points) == 1 or t <= points[0][0]:
        return float(points[0][1])
    for (t0, v0), (t1, v1) in zip(points, points[1:]):
        if t <= t1:
            span = t1 - t0
            if span <= 1e-9:
                return float(v1)
            mix = (t - t0) / span
            return float(v0) + (float(v1) - float(v0)) * mix
    return float(points[-1][1])


def load_scene(source: SceneSpec | str | Path | Mapping[str, Any]) -> SceneSpec:
    """Load a spec from an object, a mapping, or a JSON/YAML file."""
    if isinstance(source, SceneSpec):
        return source
    if isinstance(source, Mapping):
        return scene_from_mapping(source, base_dir=Path.cwd())
    path = Path(source)
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix in {".yaml", ".yml"}:
        data = yaml.safe_load(text)
    elif suffix == ".json":
        data = json.loads(text)
    else:
        raise ValueError(f"scene spec must be .json, .yaml, or .yml, got {path.name}")
    if not isinstance(data, Mapping):
        raise ValueError("scene spec must be a mapping")
    return scene_from_mapping(data, base_dir=path.parent.resolve())


def scene_from_mapping(data: Mapping[str, Any], *, base_dir: Path) -> SceneSpec:
    raw = dict(data)
    _reject_unknown(raw, _TOP_FIELDS, "scene")
    version = _as_int(raw.get("version", SCENE_VERSION), "version")
    if version != SCENE_VERSION:
        raise ValueError(f"scene version must be {SCENE_VERSION}, got {version}")
    width = _dimension(raw.get("width"), "width")
    height = _dimension(raw.get("height"), "height")
    fps = _as_int(raw.get("fps", 30), "fps")
    if fps < 1 or fps > 60:
        raise ValueError(f"fps must be from 1 to 60, got {fps}")
    seed = _as_int(raw.get("seed", 0), "seed")
    if seed < 0:
        raise ValueError(f"seed must be >= 0, got {seed}")
    stage = str(raw.get("stage", "solo")).strip().lower()
    if stage not in STAGES:
        raise ValueError(f"stage must be one of {', '.join(STAGES)}, got {stage!r}")
    lip_sync = str(raw.get("lip_sync", "envelope")).strip().lower()
    if lip_sync not in LIP_SYNC_MODES:
        raise ValueError(
            f"lip_sync must be one of {', '.join(LIP_SYNC_MODES)}, got {lip_sync!r}"
        )
    background, background_image = _background(raw.get("background", {}))
    characters = _characters(raw.get("characters"))
    if stage == "duo" and len(characters) < 2:
        raise ValueError("duo stage needs at least two characters")
    focus = raw.get("focus")
    focus_id = None if focus in (None, "") else str(focus).strip()
    ids = {character.id for character in characters}
    if focus_id is not None and focus_id not in ids:
        raise ValueError(f"focus {focus_id!r} does not match a character")
    lines = _lines(raw.get("lines", []), ids)
    camera = _camera(raw.get("camera", []))
    return SceneSpec(
        version=version,
        width=width,
        height=height,
        fps=fps,
        seed=seed,
        stage=stage,
        focus=focus_id,
        lip_sync=lip_sync,
        background=background,
        background_image=background_image,
        characters=characters,
        lines=lines,
        camera=camera,
        base_dir=Path(base_dir),
    )


def _plain_id(value: str) -> bool:
    if not value or value in {".", ".."}:
        return False
    return "/" not in value and "\\" not in value


def _characters(value: Any) -> tuple[CharacterSpec, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError("characters must be a non-empty list")
    if len(value) > MAX_CHARACTERS:
        raise ValueError(f"a scene can name at most {MAX_CHARACTERS} characters")
    characters: list[CharacterSpec] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ValueError(f"characters[{index}] must be a mapping")
        raw = dict(item)
        _reject_unknown(raw, _CHARACTER_FIELDS, f"characters[{index}]")
        character_id = str(raw.get("id", "")).strip()
        if not _plain_id(character_id):
            raise ValueError(f"characters[{index}].id must be a plain name, got {character_id!r}")
        if character_id in seen:
            raise ValueError(f"duplicate character id {character_id!r}")
        seen.add(character_id)
        label = str(raw.get("label", character_id)).strip() or character_id
        accent = str(raw.get("accent", "#8AA0B0")).strip()
        _parse_color(accent, f"characters[{index}].accent")
        facing = str(raw.get("facing", "right")).strip().lower()
        if facing not in FACINGS:
            raise ValueError(f"facing must be left or right, got {facing!r}")
        puppet = raw.get("puppet")
        puppet_path = None if puppet in (None, "") else str(puppet)
        pose = _pose_keys(raw.get("pose", []), f"characters[{index}].pose")
        expression = _expression_keys(raw.get("expression", []), f"characters[{index}].expression")
        characters.append(
            CharacterSpec(
                id=character_id,
                label=label,
                accent=accent,
                facing=facing,
                puppet=puppet_path,
                pose=pose,
                expression=expression,
            )
        )
    return tuple(characters)


def _pose_keys(value: Any, label: str) -> tuple[PoseKey, ...]:
    items = _key_list(value, label)
    keys: list[PoseKey] = []
    for index, raw in enumerate(items):
        _reject_unknown(raw, _POSE_FIELDS, f"{label}[{index}]")
        keys.append(
            PoseKey(
                t=_time(raw.get("t"), f"{label}[{index}].t"),
                head_angle=_optional_range(raw, "head_angle", -40.0, 40.0, f"{label}[{index}]"),
                y_offset=_optional_range(raw, "y_offset", -80.0, 80.0, f"{label}[{index}]"),
                scale=_optional_range(raw, "scale", 0.4, 1.8, f"{label}[{index}]"),
                glow=_optional_range(raw, "glow", 0.0, 1.0, f"{label}[{index}]"),
                brightness=_optional_range(raw, "brightness", 0.4, 1.6, f"{label}[{index}]"),
            )
        )
    keys.sort(key=lambda item: item.t)
    for field in ("head_angle", "y_offset", "scale", "glow", "brightness"):
        _reject_duplicate_times(
            [key.t for key in keys if getattr(key, field) is not None],
            f"{label}.{field}",
        )
    return tuple(keys)


def _expression_keys(value: Any, label: str) -> tuple[ExpressionKey, ...]:
    items = _key_list(value, label)
    keys: list[ExpressionKey] = []
    for index, raw in enumerate(items):
        _reject_unknown(raw, _EXPRESSION_FIELDS, f"{label}[{index}]")
        emotion = str(raw.get("emotion", "")).strip().lower()
        if not emotion:
            raise ValueError(f"{label}[{index}].emotion is required")
        keys.append(ExpressionKey(t=_time(raw.get("t"), f"{label}[{index}].t"), emotion=emotion))
    keys.sort(key=lambda item: item.t)
    _reject_duplicate_times([key.t for key in keys], label)
    return tuple(keys)


def _camera(value: Any) -> tuple[CameraKey, ...]:
    items = _key_list(value, "camera")
    mode, zoom, pan_x, pan_y = "medium", 1.0, 0.0, 0.0
    keys: list[CameraKey] = []
    for index, raw in enumerate(items):
        _reject_unknown(raw, _CAMERA_FIELDS, f"camera[{index}]")
        if "mode" in raw:
            mode = str(raw["mode"]).strip().lower()
            if mode not in CAMERA_MODES:
                raise ValueError(
                    f"camera mode must be one of {', '.join(CAMERA_MODES)}, got {mode!r}"
                )
        if "zoom" in raw:
            zoom = _required_range(raw["zoom"], 0.4, 2.5, f"camera[{index}].zoom")
        if "pan_x" in raw:
            pan_x = _required_range(raw["pan_x"], -1.0, 1.0, f"camera[{index}].pan_x")
        if "pan_y" in raw:
            pan_y = _required_range(raw["pan_y"], -1.0, 1.0, f"camera[{index}].pan_y")
        keys.append(
            CameraKey(
                t=_time(raw.get("t"), f"camera[{index}].t"),
                mode=mode,
                zoom=zoom,
                pan_x=pan_x,
                pan_y=pan_y,
            )
        )
    keys.sort(key=lambda item: item.t)
    _reject_duplicate_times([key.t for key in keys], "camera")
    return tuple(keys)


def _lines(value: Any, ids: set[str]) -> tuple[LineSpec, ...]:
    if value in (None, []):
        return ()
    if not isinstance(value, list):
        raise ValueError("lines must be a list")
    lines: list[LineSpec] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ValueError(f"lines[{index}] must be a mapping")
        raw = dict(item)
        _reject_unknown(raw, _LINE_FIELDS, f"lines[{index}]")
        speaker = str(raw.get("speaker", "")).strip()
        if speaker not in ids:
            raise ValueError(f"lines[{index}].speaker {speaker!r} does not match a character")
        start = _time(raw.get("start", 0.0), f"lines[{index}].start")
        end = None
        if "end" in raw and raw["end"] is not None:
            end = _time(raw["end"], f"lines[{index}].end")
            if end <= start:
                raise ValueError(f"lines[{index}].end must be after start")
        if "text" in raw:
            text = str(raw.get("text") or "")
        else:
            text = speaker
        emotion = str(raw.get("emotion", "neutral")).strip().lower() or "neutral"
        lines.append(LineSpec(speaker=speaker, start=start, end=end, text=text, emotion=emotion))
    lines.sort(key=lambda item: item.start)
    for prev, nxt in zip(lines, lines[1:]):
        prev_end = math.inf if prev.end is None else prev.end
        if nxt.start < prev_end - 1e-6:
            raise ValueError(
                f"lines for {prev.speaker!r} and {nxt.speaker!r} overlap at {nxt.start:.3f}s"
            )
    return tuple(lines)


def _background(value: Any) -> tuple[tuple[int, int, int], str | None]:
    if value in (None, {}):
        return (18, 22, 28), None
    if isinstance(value, str):
        return _parse_color(value, "background"), None
    if not isinstance(value, Mapping):
        raise ValueError("background must be a color or a mapping")
    raw = dict(value)
    _reject_unknown(raw, _BACKGROUND_FIELDS, "background")
    color = _parse_color(raw.get("color", "#12161C"), "background.color")
    image = raw.get("image")
    image_path = None if image in (None, "") else str(image)
    return color, image_path


def _key_list(value: Any, label: str) -> list[dict[str, Any]]:
    if value in (None, []):
        return []
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    items: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ValueError(f"{label}[{index}] must be a mapping")
        items.append(dict(item))
    return items


def _reject_unknown(raw: Mapping[str, Any], allowed: set[str], label: str) -> None:
    extra = sorted(set(raw) - allowed)
    if extra:
        names = ", ".join(extra)
        raise ValueError(f"unknown {label} field(s): {names}")


def _reject_duplicate_times(stamps: list[float], label: str) -> None:
    seen: set[float] = set()
    for stamp in stamps:
        key = round(stamp, 6)
        if key in seen:
            raise ValueError(f"{label} has two samples at t={stamp:.3f}")
        seen.add(key)


def _dimension(value: Any, label: str) -> int:
    size = _as_int(value, label)
    if size < MIN_DIMENSION or size > MAX_DIMENSION:
        raise ValueError(
            f"{label} must be from {MIN_DIMENSION} to {MAX_DIMENSION}, got {size}"
        )
    if size % 2:
        raise ValueError(f"{label} must be even (yuv420), got {size}")
    return size


def _as_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be an integer, got {value!r}")
    if isinstance(value, float) and not value.is_integer():
        raise ValueError(f"{label} must be an integer, got {value!r}")
    return int(value)


def _as_float(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number, got {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite, got {value!r}")
    return number


def _time(value: Any, label: str) -> float:
    number = _as_float(value, label)
    if number < 0:
        raise ValueError(f"{label} must be >= 0, got {number}")
    return number


def _optional_range(
    raw: Mapping[str, Any],
    field: str,
    low: float,
    high: float,
    label: str,
) -> float | None:
    if field not in raw or raw[field] is None:
        return None
    return _required_range(raw[field], low, high, f"{label}.{field}")


def _required_range(value: Any, low: float, high: float, label: str) -> float:
    number = _as_float(value, label)
    if number < low or number > high:
        raise ValueError(f"{label} must be from {low:g} to {high:g}, got {number:g}")
    return number


def _parse_color(value: Any, label: str) -> tuple[int, int, int]:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a #RRGGBB color")
    text = value.strip().lstrip("#")
    if len(text) != 6:
        raise ValueError(f"{label} must be #RRGGBB, got {value!r}")
    try:
        return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
    except ValueError as exc:
        raise ValueError(f"{label} must be #RRGGBB, got {value!r}") from exc


__all__ = [
    "CAMERA_MODES",
    "CameraKey",
    "CharacterSpec",
    "ExpressionKey",
    "LIP_SYNC_MODES",
    "LineSpec",
    "MAX_DIMENSION",
    "PoseKey",
    "SCENE_VERSION",
    "STAGES",
    "SceneSpec",
    "load_scene",
    "preview_geometry",
    "sample_linear",
    "sample_step",
    "scene_from_mapping",
]
