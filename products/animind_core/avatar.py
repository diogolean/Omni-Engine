"""Avatar sheet wrapped around Aiwake's existing generator.

``create_avatar`` does not draw its own plates. It calls
``create_puppet``: Gemini ``models/gemini-2.5-flash-image`` at 9:16, a
BiRefNet cutout, MediaPipe landmarks (OpenCV fallback), and
``auto_rig_character`` (``puppet.json`` schema 3). The person prompt and the
stick-figure prompt keep that factory's pose lock: 25° three-quarter, head
27% of the height, no drawn mouth, solid black background.

The v3 layers are seated once from the manifest framing and adapted into the
animind rig. Eyes and mouths stay on the slicer's anchors. The right view is
a mirror made at load. The scene does not rotate the puppet per frame.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image, ImageDraw

from . import image_models, mouthset, puppet_v3, qa, rig, settings, stick, styles

CANVAS = stick.CANVAS
VISEMES = mouthset.VISEMES
CHARACTER_SET_CAP_USD = 0.30
# One Gemini Flash plate, plus one mouth redraw. Both fit under the US$0.30 character cap.
_SET_ESTIMATE_USD = 0.08
_FLASH_MODEL_ID = "nano-banana"
_FINGERPRINT_VERSION = 3
SOURCES = (
    "products/aiwake/animator/factory/create_puppet.py",
    "animation/parametric/factory/rigger.py",
    "animation/parametric/factory/layer_slicer.py",
    "animation/parametric/factory/landmark_detector.py",
    "animation/parametric/factory/puppet_matrix.py",
)


class SideViewMissing(RuntimeError):
    """The profile edit failed. The front portrait is not used as a side view."""


def configured_model(model_id: str | None = None) -> str | None:
    """First implemented image model whose key is set, or None under the mock flag."""
    import os  # noqa: PLC0415

    if settings.force_mock():
        return None

    def usable(spec: image_models.ImageModel) -> bool:
        if not spec.implemented or not spec.available():
            return False
        # A 402 or a dead Gemini key must not send the next character back to Flash.
        return spec.provider != "gemini" or settings.gemini_available()

    if model_id:
        spec = image_models.selected(model_id)
        return spec.id if usable(spec) else None
    preferred = (os.getenv("ANIMIND_IMAGE_MODEL") or image_models.DEFAULT_IMAGE_MODEL).strip()
    table = image_models.models()
    if preferred in table and usable(table[preferred]):
        return preferred
    for spec in table.values():
        if usable(spec):
            return spec.id
    return None


def _ledger(cache: Path) -> dict[str, Any]:
    path = cache / "ledger.json"
    if not path.is_file():
        return {"spentUsd": 0.0, "passed": False}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"spentUsd": 0.0, "passed": False}
    data.setdefault("spentUsd", 0.0)
    data.setdefault("passed", False)
    return data


def _write_ledger(cache: Path, data: dict[str, Any]) -> None:
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "ledger.json").write_text(json.dumps(data), encoding="utf-8")


def _load_pair(folder: Path) -> tuple[Image.Image, Image.Image] | None:
    left_path = folder / "source_left.png"
    if not left_path.is_file():
        return None
    left = Image.open(left_path).convert("RGBA")
    front_path = folder / "source_front.png"
    front = Image.open(front_path).convert("RGBA") if front_path.is_file() else left.copy()
    return left, front


def _store_passing(cache: Path, left: Image.Image, front: Image.Image) -> None:
    dest = cache / "passing"
    dest.mkdir(parents=True, exist_ok=True)
    left.save(dest / "source_left.png")
    front.save(dest / "source_front.png")


def _image_ok(image: Image.Image) -> bool:
    """A bust with pixels, not an empty or flat plate that failed image QA."""
    if image.getchannel("A").getbbox() is None:
        return False
    sample = np.asarray(image.convert("RGB").resize((32, 40)), dtype=np.float32)
    return float(sample.std()) > 8.0


def create_avatar(
    description: str,
    style: str = "cartoon",
    *,
    kind: str = "rig",
    skin: Path,
    url: Callable[[Path], str],
    model_id: str | None = None,
    reuse: bool = True,
    budget: image_models.RunBudget | None = None,
    allow_mock: bool = False,
    edit_mouth: bool = True,
) -> dict[str, Any]:
    """Generate once, then reuse the cached bust for every later scene."""
    description = " ".join((description or "a person").split())[:400]
    style_id = styles.pick(style)
    stick_figure = kind == "stick" or style_id in {"stick", "southpark"}
    model_id = model_id or configured_model()
    budget = budget or image_models.run_budget()
    fingerprint = _fingerprint(description, style_id, kind, model_id or "mock", stick_figure)
    cache = settings.data_dir() / "avatar_cache" / fingerprint
    skin.mkdir(parents=True, exist_ok=True)
    ledger = _ledger(cache)
    passing = _load_pair(cache / "passing")
    latest = _load_pair(cache) if (cache / "meta.json").is_file() else None
    # A later image-QA failure must not hide a bust that already passed, and must
    # not be paid for again. The US$0.30 cap is per character set, across calls.
    over_cap = ledger["spentUsd"] + _SET_ESTIMATE_USD > CHARACTER_SET_CAP_USD + 1e-9
    cached_pair = passing if (reuse or over_cap) and passing else None
    if cached_pair is None and reuse and latest is not None:
        cached_pair = latest
    if cached_pair is None and over_cap and latest is not None:
        cached_pair = latest
    if cached_pair is not None:
        left, front = cached_pair
        meta_path = cache / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
        spec_provider = meta.get("provider") or "cached"
        spec_model = meta.get("model") or "cached"
        cost = 0.0
        mock = bool(meta.get("mock"))
        front_status = str(meta.get("frontStatus") or "cached")
        mouthless = bool(meta.get("mouthless"))
        cached = True
    elif over_cap:
        raise image_models.BudgetExceeded(
            f"character image cap US${CHARACTER_SET_CAP_USD:.2f} reached "
            f"(spent US${ledger['spentUsd']:.2f}) and no saved bust can be reused"
        )
    else:
        try:
            left, front, spec_provider, spec_model, cost, mock, front_status, mouthless = _acquire(
                description, style_id, stick_figure, skin, model_id, budget, allow_mock, ledger,
                edit_mouth=edit_mouth,
            )
        except SideViewMissing:
            if passing is None:
                raise
            left, front = passing
            spec_provider, spec_model = "cached", "cached"
            cost, mock, mouthless = 0.0, False, True
            front_status = "cached passing bust"
            if (cache / "v3" / "puppet.json").is_file():
                puppet_v3.restore(cache / "v3", skin)
        fresh = _image_ok(left) and _image_ok(front)
        ledger["spentUsd"] = round(float(ledger["spentUsd"]) + float(cost), 4)
        ledger["passed"] = bool(ledger.get("passed") or fresh)
        if fresh and not mock and (skin / "puppet.json").is_file():
            puppet_v3.snapshot(skin, cache / "v3")
        if fresh:
            _store_passing(cache, left, front)
        elif passing is not None:
            left, front = passing
            front_status = "cached passing bust"
            if (cache / "v3" / "puppet.json").is_file():
                puppet_v3.restore(cache / "v3", skin)
            fresh = True
        passed = fresh
        cache.mkdir(parents=True, exist_ok=True)
        left.save(cache / "source_left.png")
        front.save(cache / "source_front.png")
        meta = {
            "provider": spec_provider, "model": spec_model, "mock": mock,
            "frontStatus": front_status, "fingerprint": fingerprint,
            "description": description, "style": style_id, "kind": kind,
            "passed": passed, "mouthless": mouthless,
        }
        (cache / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        _write_ledger(cache, ledger)
        cached = False
    if cached and (cache / "v3" / "puppet.json").is_file():
        puppet_v3.restore(cache / "v3", skin)
    if not mock and (skin / "puppet.json").is_file():
        views = puppet_v3.adapt(skin, url)
        placed = _placed_features(skin)
        if not placed["pass"]:
            raise RuntimeError(placed.get("note") or "mouth or eyes are off the face")
        _reject_avatar_faults(skin, views)
        origin = _ensure_front(
            skin, views, url, description=description, budget=budget,
            spent=float(ledger.get("spentUsd") or 0.0) + float(cost),
            generate=not cached,
        )
        if origin == "missing":
            front_status = "missing (three-quarter plate)"
        elif origin in {"cached", "generated"}:
            front_status = "facing_front"
    else:
        views = _rig_views(left, front, skin, url, mouthless=mouthless)
    rig_path = skin / "rig.json"
    saved = json.loads(rig_path.read_text(encoding="utf-8")) if rig_path.is_file() else {}
    saved.update({
        "type": "avatar", "sources": list(SOURCES), "fingerprint": fingerprint, "mock": mock,
    })
    rig_path.write_text(json.dumps(saved), encoding="utf-8")
    failed = [] if _opaque(left) else ["empty bust"]
    failed = _with_approval(skin, failed)
    if "front" not in views:
        front_status = "missing (three-quarter plate)"
    return {
        "views": views,
        "viewStatus": {
            "left": "mock placeholder" if mock else "image-model",
            "right": "mirror of left",
            "front": front_status,
        },
        "mouthShots": {
            "source": "products.aiwake.animator.asset_generator._draw_ghibli_mouth_patch",
            "style": "south-park-organic",
            "visemes": VISEMES,
        },
        "frontShot": front_status,
        "qa": {
            "score": 100 if not any(key in failed for key in qa.APPROVAL) and "empty bust" not in failed else 0,
            "costUsd": round(cost, 8),
            "attempts": {"left": 0 if cached or mock else 1, "front": 0 if cached or front_status.startswith("left") else 1},
            "failed": failed,
        },
        "generator": "mock" if mock else spec_provider,
        "model": spec_model,
        "cached": cached,
        "fingerprint": fingerprint,
        "sources": list(SOURCES),
        "realImage": not mock,
    }


def _reserve(spec: image_models.ImageModel, budget: image_models.RunBudget, spent: float) -> None:
    """Refuse the call before it is sent when the run cap or the US$0.30 character cap is full."""
    estimate = float(spec.usdPerImage)
    if not budget.allow(estimate) or spent + estimate > CHARACTER_SET_CAP_USD + 1e-9:
        raise image_models.BudgetExceeded(
            f"{spec.id} at US${estimate:.2f} exceeds the cap "
            f"(run US${budget.remaining():.2f} left, character spent US${spent:.2f})"
        )


def _gemini_ready() -> bool:
    import os  # noqa: PLC0415

    return bool((os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip())


def _plate(
    prompt: str,
    dest: Path,
    budget: image_models.RunBudget,
    spent: float,
    *,
    reference: Path | None = None,
) -> tuple[Image.Image, float]:
    """One Gemini Flash 9:16 plate through the Aiwake generator. Refused before the call."""
    from products.aiwake.animator.factory import create_puppet  # noqa: PLC0415

    from . import telemetry  # noqa: PLC0415

    spec = image_models.selected(_FLASH_MODEL_ID)
    _reserve(spec, budget, spent)
    before = budget.spent_usd
    model = create_puppet.generate_character_image(
        prompt, dest, aspect_ratio="9:16", reference_path=reference,
    )
    cost = float(spec.usdPerImage)
    if budget.spent_usd == before:
        budget.charge(cost)
    telemetry.record(
        "image-call", provider="gemini", model=str(model or spec.model), images=1,
        cost_usd=cost, status="ok", artifact=dest, metadata={"modelId": spec.id, "aspect": "9:16"},
    )
    return Image.open(dest).convert("RGBA"), cost


def _acquire(
    description: str,
    style_id: str,
    stick_figure: bool,
    skin: Path,
    model_id: str | None,
    budget: image_models.RunBudget,
    allow_mock: bool,
    ledger: dict[str, Any],
    edit_mouth: bool = True,
) -> tuple[Image.Image, Image.Image, str, str, float, bool, str, bool]:
    del style_id, model_id
    if not _gemini_ready():
        if not allow_mock:
            raise RuntimeError("no image provider is configured")
        image = _seat(_mock_bust())
        return image, image.copy(), "mock", "placeholder", 0.0, True, "mock placeholder", False
    from products.aiwake.animator.factory.create_puppet import create_puppet  # noqa: PLC0415

    prompt = puppet_v3.avatar_prompt(description, stick=stick_figure, facing="left")
    spent = float(ledger.get("spentUsd") or 0.0)
    plate_path = skin / "_gemini.png"
    _image, cost = _plate(prompt, plate_path, budget, spent)
    _image = _prepare_plate(_image)
    _image.save(plate_path)
    from animation.parametric.factory.landmark_detector import (  # noqa: PLC0415
        RoboticLandmarkRefused,
        detect_landmarks,
    )

    try:
        marks = detect_landmarks(_image, require_human_face=True)
    except RoboticLandmarkRefused:
        raise
    if edit_mouth and _plate_has_lips(_image, marks):
        _image, extra = _edit_painted_lips(_image, plate_path, budget, spent + cost, skin)
        cost += extra
        marks = detect_landmarks(_image, require_human_face=True)
        if _plate_has_lips(_image, marks):
            raise RuntimeError("mouth still drawn under the moustache after one edit")
    character_id = "".join(ch if ch.isalnum() else "_" for ch in skin.name.lower())[:48] or "avatar"
    try:
        create_puppet(
            character_id=character_id,
            prompt=prompt,
            facing="left",
            image_path=plate_path,
            puppets_dir=skin.parent,
            rerig=(skin / "puppet.json").is_file(),
            require_human_face=True,
        )
    except (image_models.BudgetExceeded, RoboticLandmarkRefused):
        raise
    except Exception as exc:
        raise SideViewMissing(
            f"side view missing: the Aiwake rigger did not return a puppet ({type(exc).__name__})"
        ) from exc
    _clean_cutouts(skin)
    _install_human_eyes(skin)
    character = Image.open(skin / "character.png").convert("RGBA")
    spec = image_models.selected(_FLASH_MODEL_ID)
    return character, character.copy(), spec.provider, spec.model, cost, False, "aiwake v3 mouthless", True


def _mock_bust() -> Image.Image:
    """Placeholder pixels for a machine with no key. Not a drawn stick figure."""
    image = Image.new("RGBA", (320, 400), (255, 0, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle((36, 30, 150, 370), fill=(220, 40, 40, 255))
    draw.rectangle((170, 30, 284, 370), fill=(40, 40, 220, 255))
    draw.ellipse((100, 60, 220, 200), fill=(240, 210, 180, 255))
    return image


def skin_sample(image: Image.Image) -> np.ndarray:
    """Median warm skin in the upper half. Grey and pure white are not skin."""
    arr = np.asarray(image.convert("RGBA"))
    band = arr[: max(1, arr.shape[0] // 2)]
    rgb = band[:, :, :3].astype(np.int16)
    opaque = band[:, :, 3] > 40
    chroma = rgb.max(axis=2) - rgb.min(axis=2)
    warm = opaque & (rgb[:, :, 0] > rgb[:, :, 2] + 12) & (chroma > 14) & (rgb.mean(axis=2) > 70)
    if int(warm.sum()) < 20:
        warm = opaque
    if int(warm.sum()) < 8:
        return np.array([220.0, 180.0, 150.0])
    return np.median(rgb[warm], axis=0).astype(np.float64)


def skin_distance(front: Image.Image, side: Image.Image) -> float:
    """Euclidean distance between the two skin medians, in 0-255 RGB units."""
    return float(np.linalg.norm(skin_sample(front) - skin_sample(side)))


def match_skin(side: Image.Image, front: Image.Image) -> Image.Image:
    """Shift the side's warm skin onto the front portrait's skin. Beard and clothes stay."""
    delta = skin_sample(front) - skin_sample(side)
    if float(np.linalg.norm(delta)) < 8:
        return side
    arr = np.asarray(side.convert("RGBA")).astype(np.int16)
    rgb = arr[:, :, :3]
    chroma = rgb.max(axis=2) - rgb.min(axis=2)
    warm = (arr[:, :, 3] > 40) & (rgb[:, :, 0] > rgb[:, :, 2] + 8) & (chroma > 12)
    rgb[warm] = np.clip(rgb[warm] + delta, 0, 255)
    arr[:, :, :3] = rgb
    return Image.fromarray(arr.astype(np.uint8), "RGBA")


def _chin_mouth(face: tuple[int, int, int, int], eye_bottom: int) -> tuple[int, int, int, int]:
    """Mouth by proportion: below the nose, on the chin, not between the eyes."""
    fx0, fy0, fx1, fy1 = face
    chin_span = max(1, fy1 - eye_bottom)
    cy = eye_bottom + int(chin_span * 0.62)
    mw, mh = int((fx1 - fx0) * 0.28), max(8, int(chin_span * 0.16))
    cx = (fx0 + fx1) // 2
    return (cx - mw // 2, cy - mh // 2, cx + mw // 2, cy + mh // 2)


def _seat_mouth(
    face: tuple[int, int, int, int],
    eye_bottom: int,
    mouth: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    """Reject a mouth above the nose line, and shrink a beard box onto the chin."""
    _fx0, _fy0, _fx1, fy1 = face
    chin_span = max(1, fy1 - eye_bottom)
    nose = eye_bottom + int(chin_span * 0.22)
    too_tall = mouth[3] - mouth[1] > chin_span * 0.40
    too_high = (mouth[1] + mouth[3]) / 2 < nose or mouth[1] < nose
    if not too_tall and not too_high:
        return mouth
    return _chin_mouth(face, eye_bottom)


def _matte(image: Image.Image) -> Image.Image:
    image = rig._despill(image.convert("RGBA"))
    arr = np.array(image)
    red, green, blue = arr[:, :, 0].astype(int), arr[:, :, 1].astype(int), arr[:, :, 2].astype(int)
    magenta = (red > 200) & (blue > 200) & (green < 90)
    arr[:, :, 3] = np.where(magenta, 0, arr[:, :, 3])
    return Image.fromarray(arr)


def _seat(image: Image.Image) -> Image.Image:
    """Seat the bust on the Animind canvas, bottom-aligned like Aiwake's concept fitter."""
    image = _matte(image)
    box = image.getchannel("A").getbbox()
    canvas = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
    if not box:
        return canvas
    crop = image.crop(box)
    tw, th = CANVAS
    scale = min((tw * 0.88) / crop.width, (th * 0.94) / crop.height)
    resized = crop.resize(
        (max(1, round(crop.width * scale)), max(1, round(crop.height * scale))),
        Image.Resampling.NEAREST if crop.width < 400 else Image.Resampling.LANCZOS,
    )
    x = (tw - resized.width) // 2
    y = th - resized.height - int(th * 0.02)
    canvas.paste(resized, (x, max(0, y)), resized)
    return canvas


def bust_boxes(image: Image.Image) -> dict[str, tuple[int, int, int, int]]:
    """Face, eyes and mouth on a generated bust, in :func:`rig.assemble` coordinates.

    MediaPipe landmarks and Aiwake's head analyzer are accepted only when the
    mouth sits inside the face. The mecha fallback that docks the mouth on the
    torso is rejected. A bust detector then keeps the mouth on the head.
    """
    for finder in (_boxes_from_landmarks, _boxes_from_head_analyzer, _boxes_from_bust):
        try:
            boxes = finder(image)
        except Exception:
            boxes = None
        if boxes and _mouth_inside_face(boxes) and _face_sits_on_head(image, boxes):
            eye_bottom = max(boxes["left_eye"][3], boxes["right_eye"][3])
            boxes["mouth"] = _seat_mouth(boxes["face"], eye_bottom, boxes["mouth"])
            return boxes
    raise RuntimeError("could not place the mouth inside the face")


def _face_sits_on_head(image: Image.Image, boxes: dict[str, tuple[int, int, int, int]]) -> bool:
    """Reject a 'face' whose chin is down on the torso."""
    alpha = np.asarray(image.convert("RGBA"))[:, :, 3] > 24
    rows = np.nonzero(alpha.any(axis=1))[0]
    if not rows.size:
        return False
    top, bottom = int(rows.min()), int(rows.max())
    return boxes["face"][3] <= top + max(1, bottom - top) * 0.72


def _mouth_inside_face(boxes: dict[str, tuple[int, int, int, int]]) -> bool:
    face = boxes.get("face")
    mouth = boxes.get("mouth")
    if not face or not mouth:
        return False
    cx = (mouth[0] + mouth[2]) / 2
    cy = (mouth[1] + mouth[3]) / 2
    if not (face[0] <= cx <= face[2] and face[1] <= cy <= face[3]):
        return False
    if mouth[1] < face[1] - 4 or mouth[3] > face[3] + 8:
        return False
    for key in ("left_eye", "right_eye"):
        eye = boxes.get(key)
        if not eye:
            return False
        ex = (eye[0] + eye[2]) / 2
        ey = (eye[1] + eye[3]) / 2
        if not (face[0] <= ex <= face[2] and face[1] <= ey <= face[3] and ey < cy):
            return False
    return True


def _boxes_from_landmarks(image: Image.Image) -> dict[str, tuple[int, int, int, int]] | None:
    from animation.parametric.factory.landmark_detector import detect_landmarks  # noqa: PLC0415

    marks = detect_landmarks(image)
    if marks.backend == "opencv_robotic_fallback":
        return None
    width, height = image.size

    def eye_box(point, radius: float) -> tuple[int, int, int, int]:
        x, y = marks.pixel_point(point)
        r = max(6, int(radius * width))
        return _clip_box((x - r, y - r, x + r, y + r), width, height)

    mx, my = marks.pixel_point(marks.mouth_center)
    mw = max(12, int(float(marks.mouth_width) * width))
    mh = max(8, mw // 4)
    chin = marks.pixel_point(marks.chin_base)
    top = min(marks.pixel_point(marks.left_eye.center)[1], marks.pixel_point(marks.right_eye.center)[1])
    face = _clip_box((
        min(marks.pixel_point(marks.left_eye.center)[0], mx) - mw,
        max(0, top - mh * 2),
        max(marks.pixel_point(marks.right_eye.center)[0], mx) + mw,
        max(chin[1], my + mh) + 4,
    ), width, height)
    return {
        "face": face,
        "left_eye": eye_box(marks.left_eye.center, marks.left_eye.iris_radius),
        "right_eye": eye_box(marks.right_eye.center, marks.right_eye.iris_radius),
        "mouth": _clip_box((mx - mw // 2, my - mh // 2, mx + mw // 2, my + mh // 2), width, height),
    }


def _boxes_from_head_analyzer(image: Image.Image) -> dict[str, tuple[int, int, int, int]] | None:
    from products.aiwake.animator.vision.head_analyzer import analyze_head  # noqa: PLC0415

    analysis = analyze_head(image)
    width, height = image.size
    left, right = analysis.eye_left, analysis.eye_right
    if left.center[0] > right.center[0]:
        left, right = right, left
    radius = max(6, int(analysis.eye_radius))
    mouth_w = max(12, int(analysis.mouth_target_width))
    mx, my = analysis.mouth_center
    chin = analysis.chin_bbox
    face = _clip_box((
        min(left.center[0] - radius * 2, chin[0]),
        min(left.top, right.top) - radius,
        max(right.center[0] + radius * 2, chin[2]),
        max(chin[3], my + mouth_w // 4),
    ), width, height)
    return {
        "face": face,
        "left_eye": _clip_box((left.center[0] - radius, left.center[1] - radius, left.center[0] + radius, left.center[1] + radius), width, height),
        "right_eye": _clip_box((right.center[0] - radius, right.center[1] - radius, right.center[0] + radius, right.center[1] + radius), width, height),
        "mouth": _clip_box((mx - mouth_w // 2, my - max(6, mouth_w // 6), mx + mouth_w // 2, my + max(8, mouth_w // 5)), width, height),
    }


def _boxes_from_bust(image: Image.Image) -> dict[str, tuple[int, int, int, int]]:
    """Head from the neck pinch, then dark eye and mouth blobs inside that head."""
    import cv2  # noqa: PLC0415

    rgba = np.asarray(image.convert("RGBA"))
    height, width = rgba.shape[:2]
    alpha = rgba[:, :, 3] > 24
    ys, xs = np.nonzero(alpha)
    if xs.size < 40:
        raise ValueError("empty bust")
    top, bottom = int(ys.min()), int(ys.max())
    figure_h = max(1, bottom - top + 1)
    widths = alpha.sum(axis=1)
    y0 = top + int(figure_h * 0.22)
    y1 = top + int(figure_h * 0.62)
    band = widths[y0:y1]
    neck_y = y0 + int(np.argmin(band)) if band.size else top + int(figure_h * 0.42)
    head_width = float(np.median(widths[top:max(top + 1, neck_y)])) if neck_y > top else 0.0
    head_limit = neck_y if head_width > 0 and widths[neck_y] <= head_width * 0.85 else top + int(figure_h * 0.46)
    head_mask = alpha.copy()
    head_mask[head_limit + 1:, :] = False
    hys, hxs = np.nonzero(head_mask)
    if hxs.size < 20:
        raise ValueError("no head")
    face = (int(hxs.min()), int(hys.min()), int(hxs.max()) + 1, int(hys.max()) + 1)
    fx0, fy0, fx1, fy1 = face
    fw, fh = max(1, fx1 - fx0), max(1, fy1 - fy0)
    roi = rgba[fy0:fy1, fx0:fx1]
    roi_alpha = roi[:, :, 3] > 24
    lum = roi[:, :, :3].astype(np.float32).mean(axis=2)
    skin_lum = float(np.median(lum[roi_alpha])) if roi_alpha.any() else 180.0
    dark = (roi_alpha & (lum < skin_lum * 0.62)).astype(np.uint8)
    count, _labels, stats, centroids = cv2.connectedComponentsWithStats(dark, connectivity=8)
    blobs = []
    for index in range(1, count):
        x, y, bw, bh, area = (int(stats[index, i]) for i in range(5))
        if area < max(8, 0.0015 * fw * fh) or area > 0.12 * fw * fh:
            continue
        blobs.append((area, x, y, bw, bh, float(centroids[index][0]), float(centroids[index][1])))
    upper = [blob for blob in blobs if blob[6] < fh * 0.58]
    pair = _eye_pair(upper, fw, fh)
    if pair is None:
        radius = max(6, int(fw * 0.06))
        ey = fy0 + int(fh * 0.40)
        eyes = (
            (fx0 + int(fw * 0.32) - radius, ey - radius, fx0 + int(fw * 0.32) + radius, ey + radius),
            (fx0 + int(fw * 0.68) - radius, ey - radius, fx0 + int(fw * 0.68) + radius, ey + radius),
        )
    else:
        eyes = tuple(sorted((_blob_box(blob, fx0, fy0) for blob in pair), key=lambda box: box[0]))
    eye_bottom = max(eyes[0][3], eyes[1][3])
    # Nose line: a dark beard or moustache above this is not the mouth.
    nose = eye_bottom + int(0.18 * max(1, fy1 - eye_bottom))
    mouth_blobs = [
        blob for blob in blobs
        if (fy0 + blob[2]) >= nose
        and blob[6] < fh * 0.92
        and fw * 0.18 < blob[5] < fw * 0.82
        and blob[4] < fh * 0.34
    ]
    if mouth_blobs:
        # The lowest compact blob, not the largest (a beard wins on area and sits too high).
        mouth = _blob_box(max(mouth_blobs, key=lambda blob: blob[6]), fx0, fy0)
        if mouth[3] - mouth[1] < 6:
            cy = (mouth[1] + mouth[3]) // 2
            mouth = (mouth[0], cy - 4, mouth[2], cy + 6)
    else:
        mouth = _chin_mouth(face, eye_bottom)
    mouth = _seat_mouth(face, eye_bottom, mouth)
    return {
        "face": _clip_box(face, width, height),
        "left_eye": _clip_box(eyes[0], width, height),
        "right_eye": _clip_box(eyes[1], width, height),
        "mouth": _clip_box(mouth, width, height),
    }


def _eye_pair(blobs: list[tuple], face_w: int, face_h: int) -> tuple | None:
    best = None
    for index, left in enumerate(blobs):
        for right in blobs[index + 1:]:
            if abs(left[6] - right[6]) > face_h * 0.12:
                continue
            gap = abs(left[5] - right[5])
            if not (face_w * 0.12 <= gap <= face_w * 0.72):
                continue
            ratio = min(left[0], right[0]) / max(left[0], right[0])
            if ratio < 0.35:
                continue
            score = ratio - abs(left[6] - right[6]) / max(1, face_h)
            if best is None or score > best[0]:
                best = (score, left, right)
    return None if best is None else (best[1], best[2])


def _blob_box(blob: tuple, origin_x: int, origin_y: int) -> tuple[int, int, int, int]:
    _area, x, y, bw, bh, _cx, _cy = blob
    return (origin_x + x, origin_y + y, origin_x + x + bw, origin_y + y + bh)


def _clip_box(box: tuple[int, int, int, int], width: int, height: int) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = (int(v) for v in box)
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    x0 = int(np.clip(x0, 0, max(0, width - 2)))
    y0 = int(np.clip(y0, 0, max(0, height - 2)))
    x1 = int(np.clip(max(x1, x0 + 2), x0 + 2, width))
    y1 = int(np.clip(max(y1, y0 + 2), y0 + 2, height))
    return (x0, y0, x1, y1)


def _boxes_for(image: Image.Image, *, mouthless: bool) -> dict[str, tuple[int, int, int, int]]:
    try:
        boxes = bust_boxes(image)
    except RuntimeError:
        alpha = np.asarray(image.convert("RGBA"))[:, :, 3] > 24
        ys, xs = np.nonzero(alpha)
        if xs.size < 20:
            raise
        face = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.min() + (ys.max() - ys.min()) * 0.62))
        fw, fh = face[2] - face[0], face[3] - face[1]
        boxes = {
            "face": face,
            "left_eye": (face[0] + int(fw * 0.22), face[1] + int(fh * 0.28), face[0] + int(fw * 0.42), face[1] + int(fh * 0.48)),
            "right_eye": (face[0] + int(fw * 0.58), face[1] + int(fh * 0.28), face[0] + int(fw * 0.78), face[1] + int(fh * 0.48)),
            "mouth": (face[0] + int(fw * 0.3), face[1] + int(fh * 0.7), face[0] + int(fw * 0.7), face[3] - 4),
        }
    if mouthless:
        boxes["mouth"] = rig.blank_mouth_box(image, boxes["face"])
        boxes["left_eye"] = rig.iris_eye_box(image, boxes["left_eye"])
        boxes["right_eye"] = rig.iris_eye_box(image, boxes["right_eye"])
    return boxes


def _rig_views(left: Image.Image, front: Image.Image, skin: Path, url: Callable[[Path], str], *,
               mouthless: bool = False) -> dict[str, Any]:
    """Cut the bust. A mouthless head is passed as its own mouthless plate so the blur fill never runs."""

    def matte(image: Image.Image) -> Image.Image:
        return image.convert("RGBA")

    def cut(image: Image.Image, path: Path, front_view: bool) -> dict[str, Any]:
        image.save(path)
        shots = {"closed": path, "mouthless": path} if mouthless else {"closed": path}
        return rig.assemble(
            skin, shots, _boxes_for(image, mouthless=mouthless), url, matte=matte,
            front=front_view, mouthless=mouthless,
        )

    views = cut(left, skin / "_seated_left.png", False)
    views.update(cut(front, skin / "_seated_front.png", True))
    return views



def _install_human_eyes(skin: Path) -> None:
    """Cut the drawn eyes and brows. Uses the plate already on disk."""
    from animation.parametric.factory.landmark_detector import detect_landmarks  # noqa: PLC0415

    from . import human_eyes  # noqa: PLC0415

    plate_path = skin / "head.png" if (skin / "head.png").is_file() else skin / "character.png"
    plate = Image.open(plate_path).convert("RGBA")
    marks = detect_landmarks(plate, require_human_face=True)
    human_eyes.install(skin, plate, marks)


def _placed_features(skin: Path) -> dict[str, Any]:
    rig_doc = json.loads((skin / "rig.json").read_text(encoding="utf-8"))
    return qa.features_on_face(rig_doc["boxes"], rig_doc.get("irises"))


def _character_identity(name: str) -> tuple[str, str, str]:
    """``(description, folder id, display name)``."""
    if name.lower() in {"einstein", "albert", "albert-einstein", "albert_einstein"}:
        return (
            "Albert Einstein, elderly man, wild white hair, bushy white moustache, "
            "no chin beard, dark jacket, warm illustrated portrait",
            "albert_einstein",
            "Albert Einstein",
        )
    folder = "".join(ch if ch.isalnum() else "_" for ch in name.lower())[:32] or "avatar"
    return name, folder, name


def _with_approval(skin: Path, failed: list[str]) -> list[str]:
    """Add mouthAnchor / landmarks / mouthOpen when those gates fail. Other gates stay out of this list."""
    try:
        extra = qa.approval_failures(skin)
    except (OSError, ValueError, KeyError):
        extra = ["mouthAnchor"]
    return list(dict.fromkeys([*failed, *extra]))


def write_character_json(skin: Path, built: dict[str, Any], *, character_id: str, name: str, url: Callable[[Path], str]) -> Path:
    """The file ``characters.load`` reads. The CLI used to stop at the rig.

    ``qa.finalize`` rewrites ``qa.json`` from this rig. A stale score from an
    older run was what the Einstein re-rig left behind.
    """
    from datetime import datetime, timezone  # noqa: PLC0415

    qa_body = dict(built.get("qa") or {})
    try:
        report = qa.finalize(skin, float(qa_body.get("costUsd") or 0.0), dict(qa_body.get("attempts") or {}))
    except (OSError, ValueError, KeyError, StopIteration):
        report = None
    if report is not None:
        gate_failed = [key for key, gate in report["gates"].items() if not gate.get("pass")]
        kept = [item for item in list(qa_body.get("failed") or []) if item not in report["gates"]]
        qa_body["score"] = report["score"]
        qa_body["gates"] = report["gates"]
        qa_body["costUsd"] = report["costUsd"]
        qa_body["attempts"] = report.get("attempts") or qa_body.get("attempts") or {}
        qa_body["failed"] = list(dict.fromkeys([*kept, *gate_failed]))
    failed = _with_approval(skin, list(qa_body.get("failed") or [])) if report is None else list(qa_body["failed"])
    if report is not None:
        extra = [key for key in qa.APPROVAL if not (report["gates"].get(key) or {}).get("pass")]
        failed = list(dict.fromkeys([*failed, *extra]))
    qa_body["failed"] = failed
    if any(key in failed for key in qa.APPROVAL):
        qa_body["score"] = 0
    built["qa"] = qa_body
    document = {
        "id": character_id,
        "name": name,
        "status": qa.status_for(failed),
        "type": "rig",
        "style": "rig",
        "artStyle": "cartoon",
        "generator": built.get("generator") or "gemini",
        "model": built.get("model"),
        "views": built.get("views") or {},
        "viewStatus": built.get("viewStatus") or {},
        "mouthShots": built.get("mouthShots") or {},
        "frontShot": built.get("frontShot"),
        "qa": qa_body,
        "imageUrl": url(skin / "character.png") if (skin / "character.png").is_file() else None,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "pipeline": "aiwake-v3",
    }
    path = skin / "character.json"
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return path


def cached_front_plate(skin: Path) -> Path | None:
    """A frontal plate already on disk. Never the three-quarter master."""
    for name in ("source_front_mouthless.png", "source_front.png"):
        path = skin / name
        if path.is_file() and path.stat().st_size > 0:
            return path
    return None


def _drop_front(skin: Path) -> None:
    import shutil  # noqa: PLC0415

    front = skin / "views" / "front"
    if front.exists():
        shutil.rmtree(front)
    rig_front = skin / "rig_front.json"
    if rig_front.is_file():
        rig_front.unlink()


def _front_anchor_ok(skin: Path) -> bool:
    """The saved front slot is a midline mouth on a frontal plate."""
    path = skin / "views" / "front" / "view.json"
    if not path.is_file():
        return False
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return puppet_v3.frontal_anchor(doc)


def _front_plate_current(skin: Path, plate: Path) -> bool:
    """The seated front was written at or after the cached plate. Stat only."""
    view = skin / "views" / "front" / "view.json"
    try:
        return view.stat().st_mtime_ns >= plate.stat().st_mtime_ns
    except OSError:
        return False


def install_cached_front(
    skin: Path,
    url: Callable[[Path], str],
    *,
    description: str,
) -> dict[str, Any] | None:
    """Rig ``source_front.png`` into ``views/front`` without an image call.

    The side puppet stays where it is. A plate whose eyes are still
    three-quarter is not copied over, and a failed cut does not raise:
    the side rig has already succeeded. The stage directory is removed
    afterwards, so the returned URLs are read from ``views/front``.

    A front that already passes the anchor check, and whose plate has
    not been replaced since that view was written, is published as-is.
    That path does not call ``create_puppet``.
    """
    import shutil  # noqa: PLC0415

    plate = cached_front_plate(skin)
    if plate is None:
        return None
    if _front_anchor_ok(skin) and _front_plate_current(skin, plate):
        published = puppet_v3.publish_front(skin / "views" / "front", url)
        if published is not None:
            return published
    from products.aiwake.animator.factory.create_puppet import create_puppet  # noqa: PLC0415

    stage_root = skin / "_front_stage"
    if stage_root.exists():
        shutil.rmtree(stage_root)
    stage_root.mkdir(parents=True)
    staged = stage_root / "front"
    try:
        create_puppet(
            character_id="front",
            prompt=puppet_v3.avatar_prompt(description, stick=False, facing="front"),
            facing="left",
            image_path=plate,
            puppets_dir=stage_root,
            rerig=False,
            require_human_face=True,
        )
        try:
            _install_human_eyes(staged)
        except (OSError, RuntimeError, ValueError):
            pass
        views = puppet_v3.adapt(staged, url)
        front = views.get("front")
        if not front or not _front_anchor_ok(staged):
            return None
        _drop_front(skin)
        shutil.copytree(staged / "views" / "front", skin / "views" / "front")
        rig_front = staged / "rig_front.json"
        if rig_front.is_file():
            shutil.copy2(rig_front, skin / "rig_front.json")
        # adapt() above named files inside _front_stage. That directory is
        # deleted in finally, so the caller must see views/front instead.
        return puppet_v3.publish_front(skin / "views" / "front", url)
    except (OSError, RuntimeError, ValueError, TypeError):
        return None
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)


def _draw_facing_front(
    skin: Path,
    description: str,
    budget: image_models.RunBudget,
    spent: float,
) -> bool:
    """One frontal plate, only when the side view is not already front."""
    if not _gemini_ready():
        return False
    dest = skin / "source_front.png"
    reference = next(
        (path for path in (skin / "_gemini.png", skin / "character.png") if path.is_file()),
        None,
    )
    try:
        _plate(
            puppet_v3.avatar_prompt(description, stick=False, facing="front"),
            dest, budget, spent, reference=reference,
        )
    except (OSError, RuntimeError, ValueError, TypeError, image_models.BudgetExceeded):
        dest.unlink(missing_ok=True)
        return False
    return dest.is_file() and dest.stat().st_size > 0


def _ensure_front(
    skin: Path,
    views: dict[str, Any],
    url: Callable[[Path], str],
    *,
    description: str,
    budget: image_models.RunBudget,
    spent: float,
    generate: bool,
) -> str:
    """Fill ``views/front`` from a cached frontal plate, or draw one on create.

    A slot that already has a midline frontal anchor is published and
    kept. That path does not call ``create_puppet``, even when
    ``source_front.png`` is a different file from the side plate.
    A three-quarter plate tagged as front does not pass the anchor
    check and is still rebuilt or removed.

    Returns ``kept`` when the frontal slot was already seated,
    ``cached`` or ``generated`` when a dedicated front plate was seated,
    and ``missing`` when the slot stays empty.
    """
    if _front_anchor_ok(skin):
        published = puppet_v3.publish_front(skin / "views" / "front", url)
        if published is not None:
            views["front"] = published
        return "kept"
    plate = cached_front_plate(skin)
    if plate is not None:
        installed = install_cached_front(skin, url, description=description)
        if installed is not None:
            views["front"] = installed
            return "cached"
    if generate and cached_front_plate(skin) is None:
        if _draw_facing_front(skin, description, budget, spent):
            installed = install_cached_front(skin, url, description=description)
            if installed is not None:
                views["front"] = installed
                return "generated"
            (skin / "source_front.png").unlink(missing_ok=True)
    if not _front_anchor_ok(skin):
        views.pop("front", None)
        _drop_front(skin)
        return "missing"
    return "side"


def cached_plate(skin: Path) -> Path | None:
    """A plate already paid for. Re-rigging must not call the image model.

    Older caches keep the mouthless plate as ``source_mouthless.png`` and never
    copied it to ``source_original.png``. Prefer that mouthless file.
    """
    for name in (
        "source_mouthless.png",
        "source_closed.png",
        "source_front.png",
        "_gemini.png",
        "source_original.png",
        "_plate.png",
    ):
        path = skin / name
        if path.is_file() and path.stat().st_size > 0:
            return path
    return None


def _plate_has_lips(image: Image.Image, marks: Any) -> bool:
    """Lip colour inside the landmark lip ring. The face box is the fallback."""
    ring = tuple(getattr(marks, "lip_ring", ()) or ())
    if len(ring) >= 3:
        return qa.lips_in_polygon(image, ring)
    box = getattr(marks, "head_bbox", None)
    return bool(box) and qa.mouth_under_moustache_has_ink(image, box)


def _prepare_plate(image: Image.Image) -> Image.Image:
    return puppet_v3.clean_border_white(puppet_v3.drop_white_footer(image))


def _edit_painted_lips(
    image: Image.Image,
    dest: Path,
    budget: image_models.RunBudget,
    spent: float,
    skin: Path,
) -> tuple[Image.Image, float]:
    """One budget-checked Gemini edit. The plate is the reference."""
    reference = skin / "_gemini_ref.png"
    image.save(reference)
    edited, cost = _plate(puppet_v3.MOUTH_EDIT_PROMPT, dest, budget, spent, reference=reference)
    edited = _prepare_plate(edited)
    edited.save(dest)
    return edited, cost


def _clean_cutouts(skin: Path) -> None:
    for name in ("character.png", "head.png", "body.png", "collar.png"):
        path = skin / name
        if path.is_file():
            puppet_v3.clean_border_white(Image.open(path).convert("RGBA")).save(path)


def _reject_avatar_faults(skin: Path, views: dict[str, Any]) -> None:
    """A rig with a robot mouth, a jumping scale, or a double brow does not pass."""
    view = views.get("left") or next(iter(views.values()))
    faults = qa.human_avatar_faults(view, root=skin / "views" / "left")
    if faults:
        raise RuntimeError("human avatar failed QA: " + "; ".join(faults))


def rerig_from_plate(
    skin: Path,
    *,
    description: str,
    url: Callable[[Path], str],
    character_id: str,
    name: str,
    edit: bool = True,
    budget: image_models.RunBudget | None = None,
) -> dict[str, Any]:
    """Cut the saved plate again. Gemini runs only to blank a painted mouth."""
    from animation.parametric.factory.landmark_detector import detect_landmarks  # noqa: PLC0415
    from products.aiwake.animator.factory.create_puppet import create_puppet  # noqa: PLC0415

    plate = cached_plate(skin)
    if plate is None:
        raise FileNotFoundError(f"no cached plate in {skin}; nothing to re-rig without an image call")
    fixed = _prepare_plate(Image.open(plate).convert("RGBA"))
    fixed_path = skin / "_plate.png"
    fixed.save(fixed_path)
    marks = detect_landmarks(fixed, require_human_face=True)
    cost = 0.0
    if edit and _plate_has_lips(fixed, marks):
        budget = budget or image_models.run_budget()
        fixed, cost = _edit_painted_lips(fixed, fixed_path, budget, 0.0, skin)
        marks = detect_landmarks(fixed, require_human_face=True)
        if _plate_has_lips(fixed, marks):
            raise RuntimeError("mouth still drawn under the moustache after one edit")
    prompt = puppet_v3.avatar_prompt(description, stick=False, facing="left")
    create_puppet(
        character_id=character_id,
        prompt=prompt,
        facing="left",
        image_path=fixed_path,
        puppets_dir=skin.parent,
        rerig=(skin / "puppet.json").is_file(),
        require_human_face=True,
    )
    _clean_cutouts(skin)
    _install_human_eyes(skin)
    views = puppet_v3.adapt(skin, url)
    placed = _placed_features(skin)
    if not placed["pass"]:
        raise RuntimeError(placed.get("note") or "mouth or eyes are off the face")
    _reject_avatar_faults(skin, views)
    # Re-rig never draws a new plate. A frontal views/front is kept.
    # source_front.png is rigged only when that slot is missing or not frontal.
    origin = _ensure_front(
        skin, views, url, description=description,
        budget=budget or image_models.run_budget(), spent=cost, generate=False,
    )
    front_status = "facing_front" if origin != "missing" else "missing (three-quarter plate)"
    built = {
        "views": views,
        "viewStatus": {"left": "image-model", "right": "mirror of left", "front": front_status},
        "mouthShots": {"visemes": list(VISEMES)},
        "frontShot": front_status,
        "qa": {"score": 100, "costUsd": round(cost, 4), "failed": []},
        "generator": "gemini",
        "model": "cached-plate",
        "cached": True,
    }
    write_character_json(skin, built, character_id=character_id, name=name, url=url)
    return built


def main(argv: list[str] | None = None) -> None:
    """Generate one mouthless character, or re-rig a plate that is already cached.

    ``ANIMIND_RUN_BUDGET_USD=0.20 python -m products.animind_core.avatar einstein``

    ``python -m products.animind_core.avatar rerig einstein``
    """
    import argparse

    parser = argparse.ArgumentParser(prog="python -m products.animind_core.avatar")
    parser.add_argument("name", nargs="*", default=["einstein"])
    parser.add_argument("--rerig", action="store_true", help="re-rig the cached plate; no new plate")
    parser.add_argument("--no-edit", action="store_true", help="do not call Gemini to blank painted lips")
    args = parser.parse_args(argv)
    tokens = list(args.name or ["einstein"])
    rerig = bool(args.rerig)
    if tokens and tokens[0] == "rerig":
        rerig = True
        tokens = tokens[1:] or ["einstein"]
    description, folder, display = _character_identity(tokens[0] if tokens else "einstein")
    skin = settings.data_dir() / "assets" / "puppets" / folder

    def url(path: Path) -> str:
        try:
            return "/v1/files/" + path.relative_to(settings.data_dir() / "assets").as_posix()
        except ValueError:
            return path.name

    edit = not args.no_edit
    if rerig:
        built = rerig_from_plate(
            skin, description=description, url=url, character_id=folder, name=display, edit=edit,
        )
    else:
        built = create_avatar(description, "cartoon", skin=skin, url=url, edit_mouth=edit)
        write_character_json(skin, built, character_id=folder, name=display, url=url)
    print(
        f"avatar {folder} cost US${built['qa']['costUsd']:.4f} "
        f"mouthless={built.get('frontShot')} cached={built['cached']}"
    )


def _opaque(image: Image.Image) -> bool:
    return image.getchannel("A").getbbox() is not None


def _fingerprint(description: str, style_id: str, kind: str, model_id: str, stick_figure: bool) -> str:
    payload = json.dumps(
        {"description": description, "style": style_id, "kind": kind, "model": model_id, "stick": stick_figure, "v": _FINGERPRINT_VERSION},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:24]


if __name__ == "__main__":
    main()

