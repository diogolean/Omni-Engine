"""Dock a mouthless head into a headless body's collar.

Port of Aiwake's ``ParametricPuppetContract.fit_puppet`` and the collar sink in
``dock_socket`` / ``create_canonical_master``. The chin overlaps the collar so
the animated mouth, drawn later on blank skin, never sits on a painted lip.
"""
from __future__ import annotations

from typing import Any

import cv2
import numpy as np
from PIL import Image


CANVAS_W = 640
CANVAS_H = 800
# 600 / 1920 and 0.423 of the canvas, the Aiwake broadcast lines scaled to this stage.
CANONICAL_EYE_Y = 250
HEAD_HEIGHT = 338
BODY_TO_HEAD = 1.40
BODY_SCALE_Y = 1.15
CHIN_SINK_PX = 16
COLLAR_RIM = 0.17


def _rgba(image: Image.Image | np.ndarray) -> np.ndarray:
    if isinstance(image, np.ndarray):
        arr = image
        if arr.ndim == 2:
            arr = np.stack([arr, arr, arr, np.full_like(arr, 255)], axis=2)
        elif arr.shape[2] == 3:
            alpha = np.full(arr.shape[:2] + (1,), 255, np.uint8)
            arr = np.concatenate([arr, alpha], axis=2)
        return np.ascontiguousarray(arr)
    return np.asarray(image.convert("RGBA"))


def fit_puppet(
    head_img: Image.Image | np.ndarray,
    body_img: Image.Image | np.ndarray,
    view_name: str = "facing_front",
    body_bulk_ratio: float = BODY_TO_HEAD,
    target_head_h: int | None = None,
) -> tuple[np.ndarray, np.ndarray, int, float]:
    """Scale the head to the envelope and the body to ``body_bulk_ratio`` times that width."""
    head_raw, body_raw = _rgba(head_img), _rgba(body_img)
    target = HEAD_HEIGHT if target_head_h is None else int(target_head_h)
    h_scale = target / max(1, head_raw.shape[0])
    head = cv2.resize(head_raw, None, fx=h_scale, fy=h_scale, interpolation=cv2.INTER_LANCZOS4)
    target_body_w = max(1, int(head.shape[1] * body_bulk_ratio))
    b_scale = target_body_w / max(1, body_raw.shape[1])
    body = cv2.resize(
        body_raw, None, fx=b_scale, fy=b_scale * BODY_SCALE_Y, interpolation=cv2.INTER_LANCZOS4,
    )
    if view_name == "facing_left":
        stage_x, tilt = int(CANVAS_W * 585 / 1080), -3.5
    elif view_name == "facing_right":
        stage_x, tilt = int(CANVAS_W * 495 / 1080), 3.5
    else:
        stage_x, tilt = CANVAS_W // 2, 0.0
    return head, body, stage_x, tilt


def dock_head_into_collar(
    head: np.ndarray,
    body: np.ndarray,
    *,
    view_name: str = "facing_front",
    stage_x: int | None = None,
    chin_sink_px: int = CHIN_SINK_PX,
) -> tuple[np.ndarray, np.ndarray, tuple[int, int], tuple[int, int]]:
    """Sink the chin into the collar. The body's top rim is the collar."""
    if stage_x is None:
        stage_x = {"facing_left": int(CANVAS_W * 585 / 1080), "facing_right": int(CANVAS_W * 495 / 1080)}.get(
            view_name, CANVAS_W // 2,
        )
    collar_ratio = {"facing_left": 0.46, "facing_right": 0.54}.get(view_name, 0.50)
    pivot_x = head.shape[1] // 2
    eye_offset = int(head.shape[0] * 0.42)
    head_y = CANONICAL_EYE_Y - eye_offset
    head_x = int(stage_x) - pivot_x
    chin_y = int(head.shape[0] * 0.95)
    collar_rim_y = int(body.shape[0] * COLLAR_RIM)
    body_x = (head_x + pivot_x) - int(body.shape[1] * collar_ratio)
    body_y = (head_y + chin_y) - collar_rim_y
    head_y += int(chin_sink_px)
    return head, body, (int(head_x), int(head_y)), (int(body_x), int(body_y))


def _paste(canvas: np.ndarray, sprite: np.ndarray, xy: tuple[int, int]) -> None:
    x, y = xy
    h, w = sprite.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(canvas.shape[1], x + w), min(canvas.shape[0], y + h)
    if x1 <= x0 or y1 <= y0:
        return
    src = sprite[y0 - y:y1 - y, x0 - x:x1 - x]
    dst = canvas[y0:y1, x0:x1]
    alpha = src[:, :, 3:4].astype(np.float32) / 255.0
    dst[:, :, :3] = (dst[:, :, :3] * (1 - alpha) + src[:, :, :3] * alpha).astype(np.uint8)
    dst[:, :, 3] = np.maximum(dst[:, :, 3], src[:, :, 3])


def compose(
    head: np.ndarray,
    body: np.ndarray,
    head_xy: tuple[int, int],
    body_xy: tuple[int, int],
    canvas_size: tuple[int, int] = (CANVAS_W, CANVAS_H),
) -> Image.Image:
    """Body first, then the head, so the chin covers the collar."""
    canvas = np.zeros((canvas_size[1], canvas_size[0], 4), np.uint8)
    _paste(canvas, body, body_xy)
    _paste(canvas, head, head_xy)
    return Image.fromarray(canvas, "RGBA")


def assemble(
    head_img: Image.Image | np.ndarray,
    body_img: Image.Image | np.ndarray,
    view_name: str = "facing_front",
) -> dict[str, Any]:
    """Fitted puppet: full-body image, the eye line, and the head box on that image."""
    head, body, stage_x, _tilt = fit_puppet(head_img, body_img, view_name=view_name)
    head, body, head_xy, body_xy = dock_head_into_collar(head, body, view_name=view_name, stage_x=stage_x)
    image = compose(head, body, head_xy, body_xy)
    eye_y = head_xy[1] + int(head.shape[0] * 0.42)
    head_box = (
        max(0, head_xy[0]),
        max(0, head_xy[1]),
        min(CANVAS_W, head_xy[0] + head.shape[1]),
        min(CANVAS_H, head_xy[1] + head.shape[0]),
    )
    return {
        "image": image,
        "eyeY": eye_y,
        "headBox": head_box,
        "headXY": head_xy,
        "bodyXY": body_xy,
        "mouthless": True,
    }
