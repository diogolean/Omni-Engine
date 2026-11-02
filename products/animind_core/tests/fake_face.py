"""A FaceLandmarker stand-in so avatar tests never touch MediaPipe or the network."""
from __future__ import annotations

import numpy as np
from PIL import Image

from animation.parametric.factory.landmark_detector import EyeLandmark, FacialLandmarks, Point3D


def fake_human_landmarks(image: Image.Image, *, require_human_face: bool = False) -> FacialLandmarks:
    """Eyes and a mouth on the head. Backend is Tasks, not the robotic fallback."""
    del require_human_face
    arr = np.asarray(image.convert("RGBA"))
    height, width = arr.shape[:2]
    alpha = arr[:, :, 3] > 8
    ys, xs = np.nonzero(alpha)
    if xs.size:
        x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
    else:
        x0, y0, x1, y1 = 0, 0, width, height
    head_bottom = y0 + max(8, int((y1 - y0) * 0.90))
    head = (x0, y0, x1, min(height, head_bottom))
    span_x = max(1, head[2] - head[0])
    span_y = max(1, head[3] - head[1])

    def uv(px: int, py: int) -> Point3D:
        return Point3D((px - head[0]) / span_x, (py - head[1]) / span_y, 0.0)

    left = (head[0] + int(span_x * 0.35), head[1] + int(span_y * 0.38))
    right = (head[0] + int(span_x * 0.65), head[1] + int(span_y * 0.38))
    mouth = (head[0] + span_x // 2, head[1] + int(span_y * 0.72))
    chin = (mouth[0], head[3] - 1)
    return FacialLandmarks(
        left_eye=EyeLandmark(uv(*left), 0.04),
        right_eye=EyeLandmark(uv(*right), 0.04),
        mouth_center=uv(*mouth),
        mouth_width=0.16,
        jawline=(),
        chin_base=uv(*chin),
        neck_pivot=uv(chin[0], min(height - 1, head[3] + 2)),
        head_bbox=head,
        confidence=0.95,
        backend="mediapipe_tasks",
    )


def patch_human_landmarks(monkeypatch) -> None:
    """Avatar builds must not fall through to ``opencv_robotic_fallback``."""
    monkeypatch.setattr(
        "animation.parametric.factory.landmark_detector.detect_landmarks",
        fake_human_landmarks,
    )
    monkeypatch.setattr(
        "animation.parametric.factory.rigger.detect_landmarks",
        fake_human_landmarks,
    )
