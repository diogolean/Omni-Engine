"""A puppet with a fixed roll renders on the same per-frame path as an upright one."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

cv2 = pytest.importorskip("cv2")
import numpy as np

import animation.parametric.compositor as compositor_impl
import animation.parametric.puppet as puppet_impl
from animation.parametric.animator_types import REST_VISEME, SpeakerStyle

pytestmark = pytest.mark.timeout(300)

ROLLED = "rolled_host"
UPRIGHT = "upright_host"
WIDTH, HEIGHT = 1080, 1920


@pytest.fixture
def rigs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    puppet_impl.use_placeholder_asset_provider()
    monkeypatch.setattr(compositor_impl, "DEEPSEEK_LOCKED_IDS", frozenset({ROLLED}))
    monkeypatch.setattr(compositor_impl, "DEEPSEEK_ROLL_CCW_DEG", 5.0)
    return {
        cid: puppet_impl.PuppetRig(puppet_impl.PuppetSkin.load_or_create(tmp_path / cid))
        for cid in (ROLLED, UPRIGHT)
    }


def _camera(rig, *, apply_roll: bool):
    return compositor_impl._HeroCamera(
        rig,
        SpeakerStyle(character_id=rig.skin.character_id, label="Host"),
        target_width=WIDTH,
        target_height=HEIGHT,
        zoom=compositor_impl.CAMERA_NORMAL_ZOOM,
        apply_roll=apply_roll,
    )


def _draw(camera, frame: np.ndarray, t: float) -> None:
    camera.render_dirty(
        frame,
        t=t,
        viseme=REST_VISEME,
        eye_state=0,
        rms=0.0,
        emphasis_threshold=1.0,
        brow_emphasis_threshold=1.0,
        is_speaking=False,
    )


def _ms_per_frame(compositor, speaker: str, frames: int = 40) -> float:
    camera = compositor._camera[speaker]
    base = compositor._speaker_base[speaker][0]
    _draw(camera, base.copy(), 0.0)  # warm the sprite cache
    start = time.perf_counter()
    for index in range(frames):
        _draw(camera, base.copy(), index / 30.0)
    return (time.perf_counter() - start) * 1000.0 / frames


def test_rolled_puppet_costs_the_same_per_frame(rigs) -> None:
    compositor = compositor_impl.ShotReverseShotCompositor(
        rigs=rigs,
        styles={cid: SpeakerStyle(character_id=cid, label=cid) for cid in rigs},
        width=WIDTH,
        height=HEIGHT,
    )
    assert compositor._camera[ROLLED].roll_deg == pytest.approx(5.0)
    assert compositor._camera[UPRIGHT].roll_deg == 0.0
    rolled = min(_ms_per_frame(compositor, ROLLED) for _ in range(3))
    upright = min(_ms_per_frame(compositor, UPRIGHT) for _ in range(3))
    assert rolled <= upright * 1.5 + 0.5, (rolled, upright)


def test_baked_roll_matches_rotating_the_whole_puppet(rigs) -> None:
    rig = rigs[ROLLED]
    rolled = _camera(rig, apply_roll=True)
    upright = _camera(rig, apply_roll=False)
    background = np.full((HEIGHT, WIDTH, 3), (40, 90, 140), dtype=np.uint8)

    frame = background.copy()
    body, body_x, body_y = rolled.placed_body(0)
    compositor_impl._alpha_blend_paste(frame, body, body_x, body_y)
    _draw(rolled, frame, 0.0)

    # Reference: draw the upright puppet over black and over white, recover
    # straight alpha from the pair, rotate the whole plate, then composite.
    layers = []
    for fill in (0, 255):
        camera = _camera(rig, apply_roll=False)
        canvas = np.full((HEIGHT, WIDTH, 3), fill, dtype=np.uint8)
        body, body_x, body_y = camera.placed_body(0)
        compositor_impl._alpha_blend_paste(canvas, body, body_x, body_y)
        _draw(camera, canvas, 0.0)
        layers.append(canvas.astype(np.float32))
    on_black, on_white = layers
    alpha = np.clip(255.0 - (on_white - on_black).mean(axis=2), 0, 255)
    color = np.where(alpha[..., None] > 0, on_black * 255.0 / np.maximum(alpha, 1)[..., None], 0)
    rgba = np.dstack((np.clip(color, 0, 255), alpha)).astype(np.uint8)
    rotated = cv2.warpAffine(
        rgba,
        rolled._roll_matrix,
        (WIDTH, HEIGHT),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0, 0),
    )
    reference = background.copy()
    compositor_impl._alpha_blend_paste(reference, rotated, 0, 0)

    diff = np.abs(frame.astype(np.int16) - reference.astype(np.int16)).max(axis=2)
    assert diff.mean() < 1.0, diff.mean()
    assert (diff > 32).mean() < 0.002, (diff > 32).mean()
    # The roll really moved pixels relative to the upright pose.
    upright_frame = background.copy()
    body, body_x, body_y = upright.placed_body(0)
    compositor_impl._alpha_blend_paste(upright_frame, body, body_x, body_y)
    assert np.abs(frame.astype(np.int16) - upright_frame.astype(np.int16)).max(axis=2).mean() > diff.mean()
