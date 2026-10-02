# -*- coding: utf-8 -*-
"""Shot-reverse-shot cinematic director.

Canvas: 1080x1920 vertical with a full-bleed cinematic portrait:

===========  ==================  ====================================
Band         Pixels (y)          Content
===========  ==================  ====================================
Top 15%      0 - 288             Clean atmospheric headroom
Middle 60%   288 - 1440          Hero avatar, medium close-up
Bottom 25%   1440 - 1920         Full-bleed torso + karaoke overlay
===========  ==================  ====================================

**Exactly one character is ever rendered.** This is not a split screen with
one side hidden: on a given frame the inactive character's rig is never
composed, never pasted, never transformed — it simply does not participate.
The camera hard-cuts on ``DialogueTurn.start_time``, switching hero
character and camera angle atomically in the same frame.

The artist cels already carry their intended three-quarter perspective.
The camera preserves it with strict uniform scaling—no synthetic yaw warp
or independent-axis stretch is applied.
"""
from __future__ import annotations

import math
import logging
from pathlib import Path
from typing import Iterator, Sequence

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps

from .puppet import (
    PuppetRig,
    emotion_brow_state,
    onset_rest_mouth,
)
from .factory.puppet_matrix import PUPPET_MATRIX, solve_puppet_matrix
from .types import REST_VISEME, AnalyzedAudio, SpeakerStyle

_LOG = logging.getLogger("animator.compositor")

# -- Band geometry (fractions of canvas height) ----------------------------- #
HUD_BAND_FRAC = 0.0
HERO_BAND_FRAC = 0.75
SUBTITLE_BAND_FRAC = 0.25

# -- Hero framing ----------------------------------------------------------- #
#: Compatibility default for non-production callers that omit target width.
HERO_CONTENT_WIDTH = 870
# Kept as a compatibility export; full-bleed V2 composition uses no HUD.
HERO_ELEVATION_PX = 0
#: Legacy compatibility constant. Artist cels already carry this approximate
#: three-quarter yaw; the compositor no longer applies a second warp.
HERO_YAW_DEG = 25.0
CAMERA_NORMAL = "normal"
CAMERA_TIGHT = "tight"
CAMERA_NORMAL_ZOOM = 0.95
# Compatibility cameras (the v1 pilot path) render 5% above a 1.0 fit
# and sit 30px lower in the frame.
PRESENT_SCALE = 1.05
PRESENT_DROP_PX = 30
# ChatGPT and Claude, every view: another +5% on the present fit, and 70px
# lower (the prior 20px plus another 50px). Screen X: ChatGPT toward the
# viewer's right, Claude toward the viewer's left.
DEBATER_PRESENT_IDS = frozenset({"chatgpt_cyborg_v1", "claude_cyborg_v1"})
DEBATER_EXTRA_SCALE = 1.05
DEBATER_EXTRA_DROP_PX = 70
DEBATER_SHIFT_X = {
    "chatgpt_cyborg_v1": 35,
    "claude_cyborg_v1": -35,
}
# Claude, every view and every scene: 120px plus another 30px down.
CLAUDE_EXTRA_DROP_PX = 150
# ChatGPT, every view and every scene: 30px plus another 15px down, and 1% then another 1% smaller.
CHATGPT_EXTRA_DROP_PX = 30 + 15
CHATGPT_SCALE = 0.99 * 0.99
# Only the left seat (facing screen-right): another 20px toward the viewer's left.
CHATGPT_LEFT_SHIFT_X = -20
# Locked DeepSeek plate: 9% then 2% then 1%, 100px toward the viewer's
# right, and 105px plus another 10px down.
DEEPSEEK_LOCKED_IDS = frozenset({"deepseek_cyborg_v3"})
DEEPSEEK_EXTRA_SCALE = 1.09 * 1.02 * 1.01
DEEPSEEK_SHIFT_X = -200
DEEPSEEK_SHIFT_Y = 115
# Whole-robot tilt. Positive is counter-clockwise, applied to the locked plate.
DEEPSEEK_ROLL_CCW_DEG = 5.0
CAMERA_TIGHT_ZOOM = 1.40
# 0.8 s at 30 fps: a fast initial push that settles gently on the eyes/mouth.
_ZOOM_EASE_FRAMES = 24
POST_ROLL_S = 1.5
GEMINI_LEAD_X = 420
LLAMA_LEAD_X = 660
TARGET_EYE_Y = PUPPET_MATRIX["target_eye_y"]
TARGET_HEAD_HEIGHT = PUPPET_MATRIX["target_head_height"]
LEGACY_ARTIST_V2_IDS = frozenset(
    {"gemini_cyborg_v2", "llama_cyborg_v2"}
)

# -- Idle physics ----------------------------------------------------------- #
_IDLE_GLOW = 0.10

class ShotReverseShotCompositor:
    """Renders one 1080x1920 RGB frame per call from :class:`AnalyzedAudio`.

    ``rigs`` and ``styles`` are keyed by the same ``character_id`` values the
    dialogue ledger's ``speaker`` field uses. The engine stays generic: it
    never learns what those ids mean, only that exactly one of them holds
    the camera at any instant.
    """

    def __init__(
        self,
        *,
        rigs: dict[str, PuppetRig],
        styles: dict[str, SpeakerStyle],
        width: int = 1080,
        height: int = 1920,
        enable_cta: bool = False,
        outro_start_s: float | None = None,
        outro_frame=None,
        panorama_path: Path | None = None,
    ) -> None:
        if not rigs:
            raise ValueError("shot-reverse-shot needs at least one character rig")
        self.rigs = rigs
        self.styles = styles
        self.width = width
        self.height = height

        self.hud_band = (0, int(round(height * HUD_BAND_FRAC)))
        self.hero_band = (self.hud_band[1], int(round(height * (HUD_BAND_FRAC + HERO_BAND_FRAC))))
        self.subtitle_band = (self.hero_band[1], height)
        self._outro_start_s = outro_start_s if enable_cta else None
        self._outro_frame = outro_frame if enable_cta else None
        self._panorama_path = panorama_path

        # Built once as PIL images (gradients, text, glows are expensive but
        # static) then frozen into numpy — the per-frame loop is pure
        # numpy/OpenCV and never touches PIL again.
        self._background = np.asarray(self._build_background(), dtype=np.uint8).copy()
        shared_panorama = self._load_shared_panorama()
        self._backgrounds: dict[str, np.ndarray] = {
            speaker_id: self._prepare_camera_background(
                rig,
                styles.get(speaker_id),
                shared_panorama,
            )
            for speaker_id, rig in rigs.items()
        }
        self._camera: dict[str, _HeroCamera] = {
            speaker_id: _HeroCamera(
                rig,
                styles.get(speaker_id),
                target_width=self.width,
                target_height=self.height,
                zoom=CAMERA_NORMAL_ZOOM,
            )
            for speaker_id, rig in rigs.items()
        }
        self._view_zoom = 1.0
        self._view_zoom_start = 1.0
        self._view_zoom_target = 1.0
        self._view_zoom_frame = _ZOOM_EASE_FRAMES
        self._speaker_base: dict[str, dict[int, np.ndarray]] = {}
        for speaker_id, camera in self._camera.items():
            breathing_states: dict[int, np.ndarray] = {}
            background = self._backgrounds.get(
                speaker_id, self._background
            )
            for y_offset in range(-3, 4):
                frame = background.copy()
                _alpha_blend_paste(
                    frame,
                    camera.static_body,
                    camera.body_offset_x,
                    camera.body_offset_y + y_offset,
                )
                breathing_states[y_offset] = frame
            self._speaker_base[speaker_id] = breathing_states

    # -- Static layers --------------------------------------------------- #
    def _load_shared_panorama(self) -> Image.Image | None:
        """Load one arena shared by both reverse-angle cameras."""
        if self._panorama_path is not None:
            with Image.open(self._panorama_path) as panorama:
                return panorama.convert("RGB")
        if not any(character_id.endswith("_v2") for character_id in self.rigs):
            return None
        try:
            from .asset_generator import ensure_shared_panorama  # noqa: PLC0415

            root = next(iter(self.rigs.values())).skin.root.parent
            path = ensure_shared_panorama(puppets_dir=root)
            with Image.open(path) as panorama:
                return panorama.convert("RGB")
        except Exception as exc:  # noqa: BLE001 — skin bg remains a valid fallback
            _LOG.warning("shared panorama unavailable (%s); using per-skin background", exc)
            return None

    def _prepare_camera_background(
        self,
        rig: PuppetRig,
        style: SpeakerStyle | None,
        shared_panorama: Image.Image | None,
    ) -> np.ndarray:
        """Prepare a camera-side crop of the shared room or skin ``bg.png``.

        Camera A takes the panorama's left half and receives cyan atmosphere;
        Camera B takes the right half and receives amber atmosphere. Both
        retain exactly the same horizon and floor geometry.
        """
        using_shared_panorama = shared_panorama is not None
        if using_shared_panorama:
            panorama = shared_panorama.resize(
                (self.width * 2, self.height),
                Image.Resampling.LANCZOS,
            )
            facing = (style.facing if style else "right").lower()
            left = 0 if facing == "right" else self.width
            bg = panorama.crop((left, 0, left + self.width, self.height))
        else:
            bg = rig.background.convert("RGB").resize(
                (self.width, self.height),
                Image.Resampling.LANCZOS,
            )
        prepared = np.asarray(bg, dtype=np.uint8).copy()
        if using_shared_panorama:
            prepared = np.clip(
                prepared.astype(np.float32) * 0.80,
                0,
                255,
            ).astype(np.uint8)
        return prepared

    def _build_background(self) -> Image.Image:
        """Dark cyber-gradient arena with a faint grid and vignette."""
        w, h = self.width, self.height
        top = np.array([12, 15, 26], dtype=np.float32)
        bottom = np.array([2, 3, 7], dtype=np.float32)
        ramp = np.linspace(0.0, 1.0, h, dtype=np.float32).reshape(h, 1, 1)
        gradient = top.reshape(1, 1, 3) * (1 - ramp) + bottom.reshape(1, 1, 3) * ramp
        img = Image.fromarray(np.repeat(gradient, w, axis=1).astype(np.uint8), mode="RGB")

        draw = ImageDraw.Draw(img, "RGBA")
        grid = (36, 54, 82, 26)
        step = 90
        for x in range(0, w, step):
            draw.line([(x, 0), (x, h)], fill=grid, width=1)
        for y in range(0, h, step):
            draw.line([(0, y), (w, y)], fill=grid, width=1)

        # Floor plane under the hero, stopping short of the subtitle zone so
        # nothing but karaoke text ever appears in the bottom quarter.
        floor_top = int(h * 0.52)
        floor_bottom = self.subtitle_band[0]
        vanish_x = w // 2
        for i in range(1, 7):
            frac = i / 7.0
            y = floor_top + int((floor_bottom - floor_top) * frac)
            spread = int(w * 0.70 * frac)
            draw.line([(vanish_x - spread, y), (vanish_x + spread, y)], fill=(60, 88, 130, 34), width=1)

        vignette = Image.new("L", (w, h), 0)
        ImageDraw.Draw(vignette).ellipse([-w * 0.30, -h * 0.12, w * 1.30, h * 1.02], fill=255)
        vignette = vignette.filter(ImageFilter.GaussianBlur(120))
        return Image.composite(img, Image.new("RGB", (w, h), (0, 0, 0)), vignette)

    def _tight_view(
        self,
        frame: np.ndarray,
        anchor_x: int,
        *,
        zoom: float = CAMERA_TIGHT_ZOOM,
    ) -> np.ndarray:
        """Apply a variable punch-in crop centered on the docked face."""
        crop_w = int(round(self.width / zoom))
        crop_h = int(round(self.height / zoom))
        left = int(np.clip(anchor_x - crop_w // 2, 0, self.width - crop_w))
        focal_y = int(round(self.height * 0.36))
        top = int(np.clip(focal_y - crop_h * 0.34, 0, self.height - crop_h))
        crop = frame[top : top + crop_h, left : left + crop_w]
        return cv2.resize(
            crop,
            (self.width, self.height),
            interpolation=cv2.INTER_LINEAR,
        )

    def _update_view_zoom(self, camera_tight: bool) -> float:
        """Cubic ease-out between medium and 1.40x framing over 24 frames."""
        target = CAMERA_TIGHT_ZOOM if camera_tight else 1.0
        if abs(target - self._view_zoom_target) > 1e-6:
            self._view_zoom_start = self._view_zoom
            self._view_zoom_target = target
            self._view_zoom_frame = 0
        if self._view_zoom_frame < _ZOOM_EASE_FRAMES:
            self._view_zoom_frame += 1
            progress = self._view_zoom_frame / float(_ZOOM_EASE_FRAMES)
            eased = 1.0 - (1.0 - progress) ** 3
            self._view_zoom = (
                self._view_zoom_start
                + (self._view_zoom_target - self._view_zoom_start) * eased
            )
        else:
            self._view_zoom = self._view_zoom_target
        return float(self._view_zoom)

    # -- Frame loop ------------------------------------------------------- #
    def iter_frames(self, analyzed: AnalyzedAudio) -> Iterator[np.ndarray]:
        """Yield RGB24 frames (H, W, 3) uint8, ready for the ffmpeg pipe."""
        camera_speakers: Sequence[str | None] = analyzed.active_speaker
        speech_speakers: Sequence[str | None] = (
            analyzed.speaking_speaker or analyzed.active_speaker
        )
        emphasis_thresholds: dict[str, float] = {}
        brow_thresholds: dict[str, float] = {}
        for speaker_id in self.rigs:
            levels = [
                float(level)
                for level, speaker in zip(analyzed.rms, speech_speakers)
                if speaker == speaker_id and float(level) > 0.0
            ]
            emphasis_thresholds[speaker_id] = (
                float(np.percentile(levels, 70)) if levels else 1.0
            )
            brow_thresholds[speaker_id] = (
                float(np.percentile(levels, 80)) if levels else 1.0
            )
        current = next((s for s in camera_speakers if s in self.rigs), None) or next(iter(self.rigs))
        last_turn_end_frame = max(
            (
                index + 1
                for index, speaker in enumerate(camera_speakers)
                if speaker is not None
            ),
            default=0,
        )
        post_roll_frames = int(analyzed.fps * POST_ROLL_S)
        total_frames = max(
            analyzed.n_frames,
            int(math.ceil(analyzed.duration_s * analyzed.fps)),
            last_turn_end_frame + post_roll_frames,
        )
        for index in range(total_frames):
            t = analyzed.frame_time(index)
            if (
                self._outro_start_s is not None
                and self._outro_frame is not None
                and t >= self._outro_start_s
            ):
                yield self._outro_frame(t - self._outro_start_s)
                continue
            # Eyelid alpha is stencil-clipped to each lens circle on the
            # puppet canvas before this crop, so a tight or wide frame cannot
            # reveal a lid pixel outside the optic.
            camera_speaker = (
                camera_speakers[index] if index < len(camera_speakers) else None
            )
            speaking = (
                speech_speakers[index] if index < len(speech_speakers) else None
            )
            camera_cut = camera_speaker in self.rigs and camera_speaker != current
            if camera_speaker in self.rigs:
                # Hard cut: hero and camera angle switch together
                # on this exact frame — no cross-fade, no interpolation.
                current = camera_speaker
            is_speaking = speaking == current

            active_rms = float(_sequence_at(analyzed.rms, index, 0.0))
            emotion = _sequence_at(analyzed.emotion, index, "neutral")
            camera_tight = bool(
                _sequence_at(analyzed.camera_tight, index, False)
            )
            if camera_cut:
                # View zoom belongs to a shot, not to a puppet globally. A
                # reverse cut must never inherit the prior hero's push-in and
                # visibly zoom back out on the new face (the "yo-yo" glitch).
                self._view_zoom = 1.0
                self._view_zoom_start = 1.0
                self._view_zoom_target = 1.0
                self._view_zoom_frame = _ZOOM_EASE_FRAMES
                if camera_tight:
                    # Cut to the victim immediately, then perform the one
                    # authored climax push over the silent reaction.
                    self._view_zoom_target = CAMERA_TIGHT_ZOOM
                    self._view_zoom_frame = 0
                    view_zoom = self._update_view_zoom(camera_tight)
                else:
                    view_zoom = 1.0
            else:
                view_zoom = self._update_view_zoom(camera_tight)
            camera = self._camera[current]
            breathing_y = camera.breathing_offset(t)
            head_y = camera.head_breathing_offset(t)
            if abs(camera.roll_deg) > 0.01:
                # Pad so the whole robot is on the plate before the tilt.
                # Rotating a frame-clipped paste leaves a straight cut.
                pad = 640
                plate = np.zeros((self.height + pad * 2, self.width + pad * 2, 4), dtype=np.uint8)
                _paste_rgba(
                    plate,
                    camera.static_body,
                    camera.body_offset_x + pad,
                    camera.body_offset_y + breathing_y + pad,
                )
                camera.render_dirty(
                    plate,
                    t=t,
                    viseme=_sequence_at(analyzed.viseme.get(current), index, REST_VISEME) if is_speaking else REST_VISEME,
                    eye_state=_sequence_at(analyzed.eye_state.get(current), index, 0),
                    rms=active_rms,
                    emphasis_threshold=emphasis_thresholds.get(current, 1.0),
                    brow_emphasis_threshold=brow_thresholds.get(current, 1.0),
                    is_speaking=is_speaking,
                    emotion=emotion,
                    body_offset_y=head_y,
                    plate_origin=(pad, pad),
                    collar_offset_y=breathing_y,
                )
                plate = _rotate_rgba(plate, camera.roll_deg)
                frame = self._backgrounds[current].copy()
                _alpha_blend_paste(frame, plate, -pad, -pad)
            else:
                frame = self._speaker_base[current][breathing_y].copy()
                camera.render_dirty(
                    frame,
                    t=t,
                    viseme=_sequence_at(analyzed.viseme.get(current), index, REST_VISEME) if is_speaking else REST_VISEME,
                    eye_state=_sequence_at(analyzed.eye_state.get(current), index, 0),
                    rms=active_rms,
                    emphasis_threshold=emphasis_thresholds.get(current, 1.0),
                    brow_emphasis_threshold=brow_thresholds.get(current, 1.0),
                    is_speaking=is_speaking,
                    emotion=emotion,
                    body_offset_y=head_y,
                    collar_offset_y=breathing_y,
                )
            if view_zoom > 1.001:
                frame = self._tight_view(
                    frame,
                    camera.lead_anchor_x,
                    zoom=view_zoom,
                )

            yield frame


class DualPresenceCompositor:
    """Landscape arena director that keeps both articulated rigs on screen."""

    LEFT_ANCHOR_X = 420
    RIGHT_ANCHOR_X = 1500
    TIGHT_ZOOM = 1.35

    def __init__(
        self,
        *,
        rigs: dict[str, PuppetRig],
        styles: dict[str, SpeakerStyle],
        width: int = 1920,
        height: int = 1080,
        panorama_path: Path | None = None,
    ) -> None:
        if len(rigs) != 2:
            raise ValueError("dual-presence staging requires exactly two rigs")
        self.rigs = rigs
        self.styles = styles
        self.width = width
        self.height = height
        ordered = sorted(
            rigs,
            key=lambda item: 0 if styles[item].facing.lower() == "right" else 1,
        )
        self.left_id, self.right_id = ordered
        self._cameras = {
            speaker_id: _HeroCamera(
                rig,
                styles.get(speaker_id),
                target_width=840,
                target_height=height,
                zoom=1.0,
            )
            for speaker_id, rig in rigs.items()
        }
        self._origins = {
            self.left_id: self.LEFT_ANCHOR_X
            - self._cameras[self.left_id].lead_anchor_x,
            self.right_id: self.RIGHT_ANCHOR_X
            - self._cameras[self.right_id].lead_anchor_x,
        }
        self._background = self._load_landscape_background(panorama_path)
        self._gears = self._build_gear_frames()
        self._zoom = 1.0

    def _load_landscape_background(self, path: Path | None) -> np.ndarray:
        if path is not None and Path(path).is_file():
            with Image.open(path) as opened:
                background = ImageOps.fit(
                    opened.convert("RGB"),
                    (self.width, self.height),
                    method=Image.Resampling.LANCZOS,
                    centering=(0.5, 0.48),
                )
        else:
            director = ShotReverseShotCompositor.__new__(ShotReverseShotCompositor)
            director.width = self.width
            director.height = self.height
            director.subtitle_band = (self.height, self.height)
            background = director._build_background()
        darkened = np.asarray(background, dtype=np.float32) * 0.72
        return np.clip(darkened, 0, 255).astype(np.uint8)

    @staticmethod
    def _build_gear_frames() -> tuple[np.ndarray, ...]:
        source = Image.new("RGBA", (96, 96), (0, 0, 0, 0))
        draw = ImageDraw.Draw(source, "RGBA")
        center = (48, 48)
        for tooth in range(12):
            angle = math.tau * tooth / 12.0
            x = center[0] + int(math.cos(angle) * 34)
            y = center[1] + int(math.sin(angle) * 34)
            draw.rectangle((x - 5, y - 5, x + 5, y + 5), fill=(205, 151, 55, 70))
        draw.ellipse((17, 17, 79, 79), outline=(231, 181, 75, 85), width=6)
        draw.ellipse((37, 37, 59, 59), outline=(35, 24, 10, 110), width=5)
        return tuple(
            np.asarray(
                source.rotate(
                    -(index * 15),
                    resample=Image.Resampling.BICUBIC,
                )
            ).copy()
            for index in range(24)
        )

    def _listener_emotion(self, active_emotion: str, tight: bool) -> str:
        if tight or active_emotion in {"shock", "shock_perplexed", "conceded"}:
            return "shock_perplexed"
        if active_emotion in {"deboche", "inquisitor", "disbelief"}:
            return "skeptical"
        return "neutral"

    def _tight_view(self, frame: np.ndarray, focus_x: int, zoom: float) -> np.ndarray:
        crop_w = int(round(self.width / zoom))
        crop_h = int(round(self.height / zoom))
        left = int(np.clip(focus_x - crop_w // 2, 0, self.width - crop_w))
        top = int(np.clip(int(self.height * 0.47) - crop_h // 2, 0, self.height - crop_h))
        return cv2.resize(
            frame[top : top + crop_h, left : left + crop_w],
            (self.width, self.height),
            interpolation=cv2.INTER_LINEAR,
        )

    def iter_frames(self, analyzed: AnalyzedAudio) -> Iterator[np.ndarray]:
        speech = analyzed.speaking_speaker or analyzed.active_speaker
        camera = analyzed.active_speaker
        focus = self.left_id
        for index in range(analyzed.n_frames):
            t = analyzed.frame_time(index)
            speaking = _sequence_at(speech, index, None)
            camera_speaker = _sequence_at(camera, index, None)
            if camera_speaker in self.rigs:
                focus = camera_speaker
            tight = bool(_sequence_at(analyzed.camera_tight, index, False))
            active_emotion = _sequence_at(analyzed.emotion, index, "neutral")
            frame = self._background.copy()
            rms = float(_sequence_at(analyzed.rms, index, 0.0))

            for speaker_id, hero in self._cameras.items():
                is_speaking = speaking == speaker_id
                emotion = (
                    active_emotion
                    if camera_speaker == speaker_id or is_speaking
                    else self._listener_emotion(active_emotion, tight)
                )
                origin_x = self._origins[speaker_id]
                breathing_y = hero.breathing_offset(t)
                _alpha_blend_paste(
                    frame,
                    hero.static_body,
                    origin_x + hero.body_offset_x,
                    hero.body_offset_y + breathing_y,
                )
                hero.render_dirty(
                    frame,
                    t=t,
                    viseme=(
                        _sequence_at(
                            analyzed.viseme.get(speaker_id),
                            index,
                            REST_VISEME,
                        )
                        if is_speaking
                        else REST_VISEME
                    ),
                    eye_state=_sequence_at(
                        analyzed.eye_state.get(speaker_id),
                        index,
                        0,
                    ),
                    rms=rms if is_speaking else 0.0,
                    emphasis_threshold=0.58,
                    brow_emphasis_threshold=0.72,
                    is_speaking=is_speaking,
                    emotion=emotion,
                    body_offset_y=hero.head_breathing_offset(t),
                    plate_origin=(origin_x, 0),
                    collar_offset_y=breathing_y,
                )
                # A translucent clockwork tick keeps the listener's torso alive
                # even while its mouth is at rest.
                gear = self._gears[int(t * (7 if is_speaking else 3)) % len(self._gears)]
                chest_x = (
                    self.LEFT_ANCHOR_X
                    if speaker_id == self.left_id
                    else self.RIGHT_ANCHOR_X
                )
                _alpha_blend_paste(frame, gear, chest_x - 48, int(self.height * 0.67))

            target_zoom = self.TIGHT_ZOOM if tight else 1.0
            self._zoom += (target_zoom - self._zoom) * 0.12
            if self._zoom > 1.005:
                focus_x = (
                    self.LEFT_ANCHOR_X
                    if focus == self.left_id
                    else self.RIGHT_ANCHOR_X
                )
                frame = self._tight_view(frame, focus_x, self._zoom)
            yield frame


class _HeroCamera:
    """One character's native-aspect close-up camera and idle physics."""

    def __init__(
        self,
        rig: PuppetRig,
        style: SpeakerStyle | None,
        *,
        target_width: int = HERO_CONTENT_WIDTH,
        target_height: int = 1920,
        zoom: float = 1.0,
    ) -> None:
        self.rig = rig
        self.facing = (style.facing if style else "right").lower()
        self._head_angle = 0.0
        self._head_target = 0.0
        self._emphasis_active = False
        self._emphasis_direction = -1.0 if self.facing == "left" else 1.0
        self._last_emphasis_t = -10.0
        self._emotion = "neutral"
        self._previous_emotion = "neutral"
        self._emotion_transition_frame = 3
        self._brow_state = "neutral"
        self._brow_start_angle = rig.brow_angle("neutral")
        self._brow_target_angle = self._brow_start_angle
        self._brow_angle = self._brow_start_angle
        self._brow_transition_frame = 4
        self._overlay_cache: dict[
            tuple,
            tuple[np.ndarray, np.ndarray, int, int],
        ] = {}
        self.last_brow_state = "neutral"
        self.last_brow_angle = self._brow_angle

        # Head and body were authored on one matching vertical canvas. Keep
        # that entire coordinate space intact: no top crop, no replicated
        # bottom rows, and no independent layer placement.
        crop_w, crop_h = rig.canvas_size
        self.crop = (0, 0, crop_w, crop_h)

        framing = rig.skin.framing or {}
        legacy_artist_v2 = rig.skin.character_id in LEGACY_ARTIST_V2_IDS
        # A locked view is already seated on the 1080x1920 stage. The
        # parametric solver would slide the head to the eye line and the
        # body to the foot line, which pulls that seating apart.
        locked_plate = bool(framing.get("locked_plate"))
        parametric_v3 = (
            not locked_plate
            and (
                rig.skin.character_id == "deepseek_cyborg_v3"
                or bool(framing.get("puppet_matrix"))
            )
        )
        self._matrix_normalized = parametric_v3
        production_framing = bool(framing.get("bottom_anchor")) or bool(
            framing.get("full_bleed_torso")
        ) or rig.skin.character_id in {
            "gemini_cyborg_v2",
            "llama_cyborg_v2",
        }
        present_camera = False
        if locked_plate:
            scale = min(target_width / float(crop_w), target_height / float(crop_h)) * 0.96
            if rig.skin.character_id in DEEPSEEK_LOCKED_IDS:
                scale *= DEEPSEEK_EXTRA_SCALE
        elif legacy_artist_v2:
            # Immutable camera contract copied from 54b1b5d. Do not route V2
            # artist cels through any V3 eye-line or proportion normalizer.
            scale = min(
                target_width / float(crop_w),
                target_height / float(crop_h),
            )
        elif parametric_v3:
            body_alpha = rig.body_rgba[..., 3]
            body_ys, body_xs = np.nonzero(body_alpha > 8)
            body_bbox = (
                int(body_xs.min()),
                int(body_ys.min()),
                int(body_xs.max()) + 1,
                int(body_ys.max()) + 1,
            ) if body_xs.size else (0, 0, crop_w, crop_h)
            matrix = solve_puppet_matrix(
                head_height=rig.framing_head_height,
                eye_center=(
                    (
                        rig.skin.anchors.left_eye[0]
                        + rig.skin.anchors.right_eye[0]
                    )
                    / 2.0,
                    (
                        rig.skin.anchors.left_eye[1]
                        + rig.skin.anchors.right_eye[1]
                    )
                    / 2.0,
                ),
                neck_pivot=rig.skin.anchors.neck_pivot,
                body_bbox=body_bbox,
                canvas_size=rig.canvas_size,
            )
            matrix_ratio = target_height / 1920.0
            scale = matrix.head_scale * matrix_ratio
        elif production_framing:
            scale = (
                TARGET_HEAD_HEIGHT
                * (target_height / 1920.0)
                / float(rig.framing_head_height)
            )
        else:
            # Compatibility camera: fit the cel, then apply the pilot scale.
            present_camera = True
            scale = (
                min(target_width / float(crop_w), target_height / float(crop_h))
                * PRESENT_SCALE
            )
            if rig.skin.character_id in DEBATER_PRESENT_IDS:
                scale *= DEBATER_EXTRA_SCALE
            if rig.skin.character_id == "chatgpt_cyborg_v1":
                scale *= CHATGPT_SCALE
        native_eye_x = (
            rig.skin.anchors.left_eye[0] + rig.skin.anchors.right_eye[0]
        ) / 2.0
        native_eye_y = (
            rig.skin.anchors.left_eye[1] + rig.skin.anchors.right_eye[1]
        ) / 2.0
        self.lead_anchor_x = lead_anchor_x(self.facing, target_width)
        dimension = round if legacy_artist_v2 else int
        self.out_w = max(1, dimension(crop_w * scale))
        self.out_h = max(1, dimension(crop_h * scale))
        # One scalar drives canvas, anchors, head, body, eyes, mouth and brows.
        # Integer output dimensions may differ by a sub-pixel rounding residue,
        # but no layer is ever independently widened or heightened.
        if legacy_artist_v2:
            self.scale_x = self.out_w / float(crop_w)
            self.scale_y = self.out_h / float(crop_h)
            self.scale = self.scale_x
        else:
            self.scale = float(scale)
            self.scale_x = self.scale
            self.scale_y = self.scale
        self.body_scale = self.scale
        self.body_scale_x = self.scale
        self.body_scale_y = self.scale
        self.pixel_aspect_error = abs(self.scale_x - self.scale_y) / self.scale_x
        self.roll_deg = (
            DEEPSEEK_ROLL_CCW_DEG
            if rig.skin.character_id in DEEPSEEK_LOCKED_IDS
            else 0.0
        )
        if locked_plate:
            self.offset_x = int(round((target_width - self.out_w) / 2.0))
            self.offset_y = int(round((target_height - self.out_h) / 2.0))
            if rig.skin.character_id in DEEPSEEK_LOCKED_IDS:
                self.offset_x += DEEPSEEK_SHIFT_X
                self.offset_y += DEEPSEEK_SHIFT_Y
            self.lead_anchor_x = int(round(self.offset_x + native_eye_x * self.scale))
        elif legacy_artist_v2:
            self.offset_x = (target_width - self.out_w) // 2
            self.lead_anchor_x = int(
                round(self.offset_x + native_eye_x * self.scale)
            )
        elif parametric_v3:
            width_ratio = target_width / 1080.0
            self.offset_x = int(round(matrix.head_offset_x * width_ratio))
            self.lead_anchor_x = int(
                round(self.offset_x + native_eye_x * self.scale)
            )
        elif production_framing:
            body_alpha = rig.body_rgba[..., 3]
            _body_ys, body_xs = np.nonzero(body_alpha > 8)
            native_body_center_x = (
                float(body_xs.min() + body_xs.max() + 1) * 0.5
                if body_xs.size
                else native_eye_x
            )
            # Seat from facing. Centering the body put a left-seat Gemini on the right.
            self.offset_x = place_body_offset(
                self.facing,
                native_body_center_x,
                self.scale,
                frame_width=target_width,
            )
            self.lead_anchor_x = int(
                round(self.offset_x + native_eye_x * self.scale)
            )
        else:
            body_alpha = rig.body_rgba[..., 3]
            _body_ys, body_xs = np.nonzero(body_alpha > 8)
            native_body_center_x = (
                float(body_xs.min() + body_xs.max() + 1) * 0.5
                if body_xs.size
                else float(native_eye_x)
            )
            # Same dock as the Gemini path. Canvas-center anchoring left an
            # asymmetric right-facing sprite on the right side of the frame.
            self.offset_x = place_body_offset(
                self.facing,
                native_body_center_x,
                self.scale,
                frame_width=target_width,
            )
            self.lead_anchor_x = int(
                round(self.offset_x + native_eye_x * self.scale)
            )
        if parametric_v3:
            self.body_scale = matrix.body_scale * (target_width / 1080.0)
            self.body_scale_x = self.body_scale
            self.body_scale_y = self.body_scale
            body_out_w = max(1, int(round(crop_w * self.body_scale)))
            body_out_h = max(1, int(round(crop_h * self.body_scale)))
        else:
            body_out_w = self.out_w
            body_out_h = self.out_h
        self.static_body = cv2.resize(
            self.rig.body_rgba,
            (body_out_w, body_out_h),
            interpolation=cv2.INTER_AREA,
        )
        if locked_plate:
            pass
        elif legacy_artist_v2:
            self.offset_y = target_height - self.out_h
        elif parametric_v3:
            self.offset_y = int(
                round(matrix.head_offset_y * (target_height / 1920.0))
            )
        else:
            # Sit the last opaque body row on the frame foot. Transparent
            # padding falls off-screen; nothing is painted over the PNG.
            body_rows = np.nonzero(self.rig.body_rgba[..., 3] > 8)[0]
            opaque_bottom = (
                int(body_rows.max()) + 1 if body_rows.size else rig.canvas_size[1]
            )
            self.offset_y = int(round(target_height - opaque_bottom * self.scale))
            if present_camera:
                drop = PRESENT_DROP_PX
                if rig.skin.character_id in DEBATER_PRESENT_IDS:
                    drop += DEBATER_EXTRA_DROP_PX
                if rig.skin.character_id == "claude_cyborg_v1":
                    drop += CLAUDE_EXTRA_DROP_PX
                if rig.skin.character_id == "chatgpt_cyborg_v1":
                    drop += CHATGPT_EXTRA_DROP_PX
                self.offset_y += drop
        if parametric_v3:
            self.body_offset_x = int(
                round(matrix.body_offset_x * (target_width / 1080.0))
            )
            self.body_offset_y = int(
                round(matrix.body_offset_y * (target_height / 1920.0))
            )
        else:
            self.body_offset_x = self.offset_x
            self.body_offset_y = self.offset_y
        self.eye_line_y = int(round(native_eye_y * self.scale_y + self.offset_y))

    @staticmethod
    def breathing_offset(t: float) -> int:
        """Body idle. Small enough that it rounds away except at the crest."""
        return int(np.clip(np.rint(np.sin(float(t) * 2.0) * 0.55), -1, 1))

    @staticmethod
    def head_breathing_offset(t: float) -> int:
        """The former whole-body bob, kept on the head alone."""
        return int(np.clip(np.rint(np.sin(float(t) * 2.0) * 2.5), -3, 3))

    def _scaled_overlay(
        self,
        key: tuple,
        overlay: np.ndarray,
        bbox: tuple[int, int, int, int],
        *,
        scale_x: float | None = None,
        scale_y: float | None = None,
        offset_x: int | None = None,
        offset_y: int | None = None,
    ) -> tuple[np.ndarray, np.ndarray, int, int]:
        cached = self._overlay_cache.get(key)
        if cached is not None:
            return cached
        resolved_scale_x = self.scale_x if scale_x is None else scale_x
        resolved_scale_y = self.scale_y if scale_y is None else scale_y
        resolved_offset_x = self.offset_x if offset_x is None else offset_x
        resolved_offset_y = self.offset_y if offset_y is None else offset_y
        x0, y0, x1, y1 = bbox
        out_x0 = int(round(x0 * resolved_scale_x))
        out_y0 = int(round(y0 * resolved_scale_y))
        out_x1 = int(round(x1 * resolved_scale_x))
        out_y1 = int(round(y1 * resolved_scale_y))
        resized = cv2.resize(
            overlay,
            (max(1, out_x1 - out_x0), max(1, out_y1 - out_y0)),
            interpolation=cv2.INTER_AREA,
        )
        alpha = resized[..., 3]
        alpha_3ch = cv2.merge([alpha, alpha, alpha])
        premultiplied = cv2.multiply(
            resized[..., :3],
            alpha_3ch,
            scale=1.0 / 255.0,
        )
        result = (
            premultiplied,
            cv2.bitwise_not(alpha_3ch),
            resolved_offset_x + out_x0,
            resolved_offset_y + out_y0,
        )
        self._overlay_cache[key] = result
        return result

    def render_dirty(
        self,
        frame: np.ndarray,
        *,
        t: float,
        viseme: str,
        eye_state: int,
        rms: float,
        emphasis_threshold: float,
        brow_emphasis_threshold: float,
        is_speaking: bool,
        emotion: str = "neutral",
        body_offset_y: int = 0,
        plate_origin: tuple[int, int] = (0, 0),
        collar_offset_y: int | None = None,
    ) -> None:
        """Blit one articulated head crop onto a pre-baked breathing body."""
        self._update_emotion(emotion)
        brow_state = emotion_brow_state(self._emotion)
        brow_angle = self._update_brow_angle(brow_state)
        brow_emphasized = bool(is_speaking and rms > brow_emphasis_threshold)
        self.last_brow_state = brow_state
        self.last_brow_angle = brow_angle
        head_angle = self._update_head_angle(
            t=t,
            rms=rms,
            emphasis_threshold=emphasis_threshold,
            is_speaking=is_speaking,
        )
        rest_state = onset_rest_mouth(self._emotion, viseme)
        head, bbox = self.rig.articulated_head_overlay(
            viseme=viseme,
            eye_state=eye_state,
            brow_state=brow_state,
            brow_emphasized=brow_emphasized,
            brow_angle_deg=brow_angle,
            rest_mouth_state=rest_state,
            force_static_mouth=not is_speaking,
            angle_deg=head_angle,
        )
        angle_key = round(float(head_angle) / 0.3) * 0.3
        premultiplied, inv_alpha, x, y = self._scaled_overlay(
            (
                "head",
                (viseme or REST_VISEME).upper()[:1],
                eye_state,
                brow_state,
                round(brow_angle, 2),
                rest_state
                if (viseme or REST_VISEME).upper()[:1] == REST_VISEME
                else "",
                angle_key,
            ),
            head,
            bbox,
        )
        origin_x, origin_y = plate_origin
        _paste_sprite(
            frame,
            premultiplied,
            inv_alpha,
            x + origin_x,
            y + int(body_offset_y) + origin_y,
        )
        collar = self.rig.collar_overlay()
        if collar is not None and not self._matrix_normalized:
            collar_crop, collar_bbox = collar
            collar_rgb, collar_inv_alpha, collar_x, collar_y = self._scaled_overlay(
                (
                    "collar",
                    round(self.body_scale_x, 6),
                    self.body_offset_x,
                    self.body_offset_y,
                ),
                collar_crop,
                collar_bbox,
                scale_x=self.body_scale_x,
                scale_y=self.body_scale_y,
                offset_x=self.body_offset_x,
                offset_y=self.body_offset_y,
            )
            neck_y = body_offset_y if collar_offset_y is None else collar_offset_y
            _paste_sprite(
                frame,
                collar_rgb,
                collar_inv_alpha,
                collar_x + origin_x,
                collar_y + int(neck_y) + origin_y,
            )

    def render(
        self,
        *,
        t: float,
        viseme: str,
        eye_state: int,
        rms: float,
        emphasis_threshold: float,
        is_speaking: bool,
        emotion: str = "neutral",
    ) -> np.ndarray:
        """Compose and uniformly scale this character for one frame."""
        glow = _IDLE_GLOW + (max(0.0, rms) - _IDLE_GLOW) * 0.9 if is_speaking else _IDLE_GLOW * 0.7
        head_angle = self._update_head_angle(
            t=t,
            rms=rms,
            emphasis_threshold=emphasis_threshold,
            is_speaking=is_speaking,
        )
        previous_emotion, emotion_mix = self._update_emotion(emotion)
        sprite = self.rig.compose(
            viseme=viseme,
            eye_state=eye_state,
            y_offset=0.0,
            brightness=1.0,
            glow_intensity=float(np.clip(glow, 0.0, 1.0)),
            head_angle=head_angle,
            emotion=self._emotion,
            previous_emotion=previous_emotion,
            emotion_mix=emotion_mix,
        )
        x0, y0, x1, y1 = self.crop
        return cv2.resize(
            sprite[y0:y1, x0:x1],
            (self.out_w, self.out_h),
            interpolation=cv2.INTER_AREA,
        )

    def _update_emotion(self, emotion: str) -> tuple[str, float]:
        """Ease brow posture over exactly three rendered camera frames."""
        target = (emotion or "neutral").lower()
        if target != self._emotion:
            self._previous_emotion = self._emotion
            self._emotion = target
            self._emotion_transition_frame = 0
        if self._emotion_transition_frame < 3:
            self._emotion_transition_frame += 1
        return self._previous_emotion, self._emotion_transition_frame / 3.0

    def _update_brow_angle(self, state: str) -> float:
        """Cosine-ease brow slant to a new emotion over four frames."""
        normalized = emotion_brow_state(state)
        target = self.rig.brow_angle(normalized)
        if normalized != self._brow_state or abs(target - self._brow_target_angle) > 1e-6:
            self._brow_state = normalized
            self._brow_start_angle = self._brow_angle
            self._brow_target_angle = target
            self._brow_transition_frame = 0
        if self._brow_transition_frame < 4:
            self._brow_transition_frame += 1
            progress = self._brow_transition_frame / 4.0
            eased = (1.0 - np.cos(np.pi * progress)) * 0.5
            self._brow_angle = (
                self._brow_start_angle
                + (self._brow_target_angle - self._brow_start_angle) * eased
            )
        else:
            self._brow_angle = self._brow_target_angle
        return float(self._brow_angle)

    def _update_head_angle(
        self,
        *,
        t: float,
        rms: float,
        emphasis_threshold: float,
        is_speaking: bool,
    ) -> float:
        """Ease toward brief emphasis impulses; never run a periodic oscillator."""
        if not is_speaking:
            self._head_target = 0.0
            self._emphasis_active = False
            emphasized = False
        else:
            emphasized = rms > emphasis_threshold
            if emphasized:
                if not self._emphasis_active and (t - self._last_emphasis_t) >= 0.35:
                    self._emphasis_direction *= -1.0
                    self._last_emphasis_t = t
                self._head_target = emphasis_head_target(
                    rms,
                    emphasis_threshold,
                    direction=self._emphasis_direction,
                )
            else:
                self._head_target = 0.0
            self._emphasis_active = emphasized

        easing = 0.32 if emphasized else 0.18
        self._head_angle += (self._head_target - self._head_angle) * easing
        self._head_angle = float(np.clip(self._head_angle, -1.2, 1.2))
        if not emphasized and abs(self._head_angle) < 0.01:
            self._head_angle = 0.0
        return self._head_angle


def place_body_offset(
    facing: str,
    native_body_center_x: float,
    scale: float,
    *,
    frame_width: int = 1080,
) -> int:
    """Put a right-facing body on the left, and a left-facing body on the right.

    Raises if that dock would land on the forbidden half of the frame.
    """
    lead = lead_anchor_x(facing, frame_width)
    offset = int(round(lead - native_body_center_x * scale))
    drawn = offset + native_body_center_x * scale
    side = "left" if drawn < frame_width / 2 else "right"
    if (facing or "").lower() == "right" and side != "left":
        raise ValueError("right-facing sprite would be drawn on the right")
    if (facing or "").lower() == "left" and side != "right":
        raise ValueError("left-facing sprite would be drawn on the left")
    return offset


def lead_anchor_x(facing: str, width: int = 1080) -> int:
    """Dock a right-facing hero left, and a left-facing hero right."""
    if (facing or "").lower() == "right":
        return GEMINI_LEAD_X
    if (facing or "").lower() == "left":
        return LLAMA_LEAD_X
    return width // 2


def dramatic_camera_mode(*, camera_tight: bool) -> str:
    """Choose one of two discrete precomputed viewports."""
    return CAMERA_TIGHT if camera_tight else CAMERA_NORMAL


def emphasis_head_target(rms: float, threshold: float, *, direction: float) -> float:
    """One bounded emphasis impulse; ordinary speech produces no rotation."""
    if rms <= threshold:
        return 0.0
    span = max(1e-6, 1.0 - threshold)
    strength = float(np.clip((rms - threshold) / span, 0.0, 1.0))
    return float(np.clip(direction, -1.0, 1.0)) * (0.45 + 0.75 * strength)


def _sequence_at(seq, index: int, default):
    if not seq or index >= len(seq):
        return default
    return seq[index]


def _paste_sprite(
    dest: np.ndarray,
    premultiplied_rgb: np.ndarray,
    inv_alpha_3ch: np.ndarray,
    x: int,
    y: int,
) -> None:
    """Paint a premultiplied sprite onto an RGB frame or an RGBA plate."""
    if dest.shape[-1] == 4:
        alpha = cv2.bitwise_not(inv_alpha_3ch[..., 0])
        scale = np.maximum(alpha.astype(np.float32), 1.0)[..., None]
        straight = np.clip(
            premultiplied_rgb.astype(np.float32) * (255.0 / scale),
            0,
            255,
        ).astype(np.uint8)
        _paste_rgba(dest, np.dstack((straight, alpha)), x, y)
        return
    _alpha_blend_paste_precomputed(dest, premultiplied_rgb, inv_alpha_3ch, x, y)


def _paste_rgba(dest: np.ndarray, src_rgba: np.ndarray, x: int, y: int) -> None:
    """Source-over paste of an RGBA sprite onto an RGBA plate."""
    dh, dw = dest.shape[:2]
    sh, sw = src_rgba.shape[:2]
    dst_x0, dst_y0 = max(0, x), max(0, y)
    dst_x1, dst_y1 = min(dw, x + sw), min(dh, y + sh)
    if dst_x1 <= dst_x0 or dst_y1 <= dst_y0:
        return
    src_x0, src_y0 = dst_x0 - x, dst_y0 - y
    src_x1 = src_x0 + (dst_x1 - dst_x0)
    src_y1 = src_y0 + (dst_y1 - dst_y0)
    src = src_rgba[src_y0:src_y1, src_x0:src_x1].astype(np.float32)
    dst = dest[dst_y0:dst_y1, dst_x0:dst_x1].astype(np.float32)
    src_a = src[..., 3:4] / 255.0
    dst_a = dst[..., 3:4] / 255.0
    out_a = src_a + dst_a * (1.0 - src_a)
    out_rgb = src[..., :3] * src_a + dst[..., :3] * dst_a * (1.0 - src_a)
    safe = np.maximum(out_a, 1.0 / 255.0)
    merged = np.concatenate((out_rgb / safe, out_a * 255.0), axis=2)
    dest[dst_y0:dst_y1, dst_x0:dst_x1] = np.clip(merged, 0, 255).astype(np.uint8)


def _rotate_rgba(image: np.ndarray, degrees_ccw: float) -> np.ndarray:
    """Rotate an RGBA plate counter-clockwise around the visible robot."""
    alpha = image[..., 3]
    ys, xs = np.nonzero(alpha > 8)
    if xs.size == 0:
        return image
    center = (float(xs.mean()), float(ys.mean()))
    matrix = cv2.getRotationMatrix2D(center, float(degrees_ccw), 1.0)
    return cv2.warpAffine(
        image,
        matrix,
        (image.shape[1], image.shape[0]),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0, 0),
    )


def _alpha_blend_paste(dest: np.ndarray, src_rgba: np.ndarray, x: int, y: int) -> None:
    """Alpha-composite ``src_rgba`` onto ``dest`` (RGB) at offset ``(x, y)``,
    clipped to ``dest``'s bounds. cv2-accelerated (SIMD uint8 ops) and only
    ever touches the region it actually overlaps."""
    dh, dw = dest.shape[:2]
    sh, sw = src_rgba.shape[:2]
    dst_x0, dst_y0 = max(0, x), max(0, y)
    dst_x1, dst_y1 = min(dw, x + sw), min(dh, y + sh)
    if dst_x1 <= dst_x0 or dst_y1 <= dst_y0:
        return
    src_x0, src_y0 = dst_x0 - x, dst_y0 - y
    src_x1, src_y1 = src_x0 + (dst_x1 - dst_x0), src_y0 + (dst_y1 - dst_y0)

    src_region = np.ascontiguousarray(src_rgba[src_y0:src_y1, src_x0:src_x1])
    dst_region = dest[dst_y0:dst_y1, dst_x0:dst_x1]

    alpha = src_region[..., 3]
    inv_alpha = cv2.bitwise_not(alpha)
    # cv2.merge to broadcast a single channel to 3 is ~2-3x faster than
    # np.repeat for this shape/size — measured, not assumed.
    alpha_3ch = cv2.merge([alpha, alpha, alpha])
    inv_alpha_3ch = cv2.merge([inv_alpha, inv_alpha, inv_alpha])

    fg = cv2.multiply(src_region[..., :3], alpha_3ch, scale=1.0 / 255.0)
    bg = cv2.multiply(dst_region, inv_alpha_3ch, scale=1.0 / 255.0)
    dest[dst_y0:dst_y1, dst_x0:dst_x1] = cv2.add(fg, bg)


def _alpha_blend_paste_precomputed(
    dest: np.ndarray,
    premultiplied_rgb: np.ndarray,
    inv_alpha_3ch: np.ndarray,
    x: int,
    y: int,
) -> None:
    """Blend a cached premultiplied sprite with one multiply and one add."""
    dh, dw = dest.shape[:2]
    sh, sw = premultiplied_rgb.shape[:2]
    dst_x0, dst_y0 = max(0, x), max(0, y)
    dst_x1, dst_y1 = min(dw, x + sw), min(dh, y + sh)
    if dst_x1 <= dst_x0 or dst_y1 <= dst_y0:
        return
    src_x0, src_y0 = dst_x0 - x, dst_y0 - y
    src_x1 = src_x0 + (dst_x1 - dst_x0)
    src_y1 = src_y0 + (dst_y1 - dst_y0)
    dst_region = dest[dst_y0:dst_y1, dst_x0:dst_x1]
    bg = cv2.multiply(
        dst_region,
        inv_alpha_3ch[src_y0:src_y1, src_x0:src_x1],
        scale=1.0 / 255.0,
    )
    dest[dst_y0:dst_y1, dst_x0:dst_x1] = cv2.add(
        premultiplied_rgb[src_y0:src_y1, src_x0:src_x1],
        bg,
    )


__all__ = [
    "CAMERA_NORMAL",
    "CAMERA_NORMAL_ZOOM",
    "CAMERA_TIGHT",
    "CAMERA_TIGHT_ZOOM",
    "GEMINI_LEAD_X",
    "LLAMA_LEAD_X",
    "TARGET_EYE_Y",
    "TARGET_HEAD_HEIGHT",
    "lead_anchor_x",
    "HERO_CONTENT_WIDTH",
    "HERO_ELEVATION_PX",
    "HERO_YAW_DEG",
    "HUD_BAND_FRAC",
    "DualPresenceCompositor",
    "ShotReverseShotCompositor",
    "dramatic_camera_mode",
    "emphasis_head_target",
]
