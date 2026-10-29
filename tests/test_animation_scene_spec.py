"""Scene spec parsing. The render tests cover pixels and ffmpeg."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from animation.examples.make_audio import write_sample
from animation.parametric.scene_spec import (
    load_scene,
    preview_geometry,
    sample_linear,
    sample_step,
)

_REPO = Path(__file__).resolve().parents[1]


def _spec(**extra):
    data = {
        "version": 1,
        "width": 32,
        "height": 32,
        "fps": 10,
        "characters": [{"id": "host"}],
    }
    data.update(extra)
    return data


def test_example_scene_is_1080p_landscape() -> None:
    spec = load_scene(_REPO / "animation" / "examples" / "rigly_hello" / "scene.yaml")
    assert (spec.width, spec.height, spec.fps) == (1920, 1080, 30)
    assert spec.stage == "solo"
    assert spec.lip_sync == "envelope"
    assert spec.characters[0].id == "host"
    assert spec.characters[0].pose[1].head_angle == 7
    assert spec.camera[1].mode == "closeup"
    assert spec.camera[1].zoom == 1.08
    assert spec.lines[0].text == "Hello from Rigly."


def test_json_spec_round_trip(tmp_path: Path) -> None:
    payload = _spec(seed=4, stage="solo", background="#010203")
    path = tmp_path / "scene.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    spec = load_scene(path)
    assert spec.seed == 4
    assert spec.background == (1, 2, 3)
    assert spec.base_dir == path.parent.resolve()


def test_preview_geometry_covers_1080p_aspects() -> None:
    assert preview_geometry(1920, 1080, 30) == (960, 540, 12)
    assert preview_geometry(1080, 1920, 30) == (540, 960, 12)
    assert preview_geometry(1080, 1080, 24) == (540, 540, 12)
    assert preview_geometry(96, 64, 8) == (48, 32, 8)


def test_samples_hold_and_interpolate() -> None:
    assert sample_linear([(0.0, 0.0), (1.0, 10.0)], 0.5, -1.0) == 5.0
    assert sample_linear([], 0.4, 3.0) == 3.0
    assert sample_step([(0.0, "neutral"), (1.0, "skeptical")], 1.2, "other") == "skeptical"
    assert sample_step([], 0.0, "neutral") == "neutral"


def test_camera_key_keeps_controls_it_does_not_set() -> None:
    spec = load_scene(
        _spec(
            camera=[
                {"t": 0, "mode": "medium", "zoom": 1.2, "pan_x": 0.1},
                {"t": 1, "mode": "closeup"},
            ]
        )
    )
    assert spec.camera[1].mode == "closeup"
    assert spec.camera[1].zoom == 1.2
    assert spec.camera[1].pan_x == 0.1


def test_rejects_odd_dimensions_and_oversized_frames() -> None:
    with pytest.raises(ValueError, match="even"):
        load_scene(_spec(width=31))
    with pytest.raises(ValueError, match="1920"):
        load_scene(_spec(width=3840, height=2160))


def test_rejects_unknown_fields_duplicate_ids_and_overlaps() -> None:
    with pytest.raises(ValueError, match="unknown scene"):
        load_scene(_spec(channel="demo"))
    with pytest.raises(ValueError, match="duplicate"):
        load_scene(_spec(characters=[{"id": "host"}, {"id": "host"}]))
    with pytest.raises(ValueError, match="overlap"):
        load_scene(
            _spec(
                lines=[
                    {"speaker": "host", "start": 0, "end": 2, "text": "a"},
                    {"speaker": "host", "start": 1, "end": 3, "text": "b"},
                ]
            )
        )
    with pytest.raises(ValueError, match="speaker"):
        load_scene(_spec(lines=[{"speaker": "missing", "start": 0, "text": "no"}]))
    with pytest.raises(ValueError, match="version"):
        load_scene(_spec(version=2))


def test_scene_renderer_streams_and_bounds_ffmpeg() -> None:
    source = (_REPO / "animation" / "parametric" / "scene_render.py").read_text(encoding="utf-8")
    assert "retain_frames=False" in source
    assert "encode_timeout_s" in source
    assert "moviepy" not in source.lower()
    compositor = (_REPO / "animation" / "parametric" / "scene_compositor.py").read_text(
        encoding="utf-8"
    )
    assert "yield self.frame" in compositor
    assert "moviepy" not in compositor.lower()


def test_sample_tone_is_deterministic(tmp_path: Path) -> None:
    first = write_sample(tmp_path / "a.wav", seconds=0.2)
    second = write_sample(tmp_path / "b.wav", seconds=0.2)
    assert first.read_bytes() == second.read_bytes()
    assert first.stat().st_size > 44
