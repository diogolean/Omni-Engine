"""One-call avatar sheet wrapped around Aiwake's generator.

``create_avatar(description, style)`` draws the bust with the selected image
provider. Gemini goes through ``create_puppet.generate_character_image``.
Together and OpenRouter use the same prompt contract. The right view is a
mirror of the left view. Mouths are Aiwake's organic Rhubarb bank (South Park
construction-paper shapes: ``smug_smile`` and ``stressed_grimace``). Eyes,
lids and brows are anchored with Aiwake's landmark detector.

The bust is never redrawn with stick primitives. With no key, callers that
pass ``allow_mock=True`` get an obvious placeholder; the HTTP stick fallback
stays in :mod:`products.animind_core.stick` for tests.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image, ImageDraw, ImageOps

from . import image_models, mouthset, rig, settings, stick, styles

CANVAS = stick.CANVAS
VISEMES = mouthset.VISEMES
SOURCES = (
    "products/aiwake/animator/factory/create_puppet.py",
    "products/aiwake/animator/factory/one_shot_factory.py",
    "products/aiwake/animator/factory/generate_concepts.py",
    "products/aiwake/animator/asset_generator.py",
    "products/aiwake/animator/render/facial_rig.py",
    "products/aiwake/animator/render/skia_mouths.py",
    "products/aiwake/animator/vision/head_analyzer.py",
    "animation/parametric/factory/landmark_detector.py",
    "animation/parametric/animator_types.py",
)
MOUTH_COLOURS = {
    "outline": (20, 16, 16, 255),
    "cavity": (30, 16, 18, 255),
    "teeth": (248, 246, 240, 255),
    "tongue": (196, 90, 96, 255),
    "lip": (150, 70, 74, 255),
}


@dataclass(frozen=True)
class Anchors:
    left_eye: tuple[int, int]
    right_eye: tuple[int, int]
    mouth: tuple[int, int]
    mouth_width: int
    chin: int
    neck: int
    radius: int


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
    preferred = (os.getenv("ANIMIND_IMAGE_MODEL") or "nano-banana").strip()
    table = image_models.models()
    if preferred in table and usable(table[preferred]):
        return preferred
    for spec in table.values():
        if usable(spec):
            return spec.id
    return None


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
) -> dict[str, Any]:
    """Generate once, then reuse the cached bust for every later scene."""
    description = " ".join((description or "a person").split())[:400]
    style_id = style if style == "stick" or style in styles.STYLES else styles.pick(style)
    stick_figure = kind == "stick" or style_id == "stick"
    model_id = model_id or configured_model()
    budget = budget or image_models.run_budget()
    fingerprint = _fingerprint(description, style_id, kind, model_id or "mock", stick_figure)
    cache = settings.data_dir() / "avatar_cache" / fingerprint
    skin.mkdir(parents=True, exist_ok=True)
    cached = reuse and (cache / "source_left.png").is_file() and (cache / "meta.json").is_file()
    if cached:
        meta = json.loads((cache / "meta.json").read_text(encoding="utf-8"))
        left = Image.open(cache / "source_left.png").convert("RGBA")
        front_path = cache / "source_front.png"
        front = Image.open(front_path).convert("RGBA") if front_path.is_file() else left.copy()
        spec_provider = meta.get("provider") or "mock"
        spec_model = meta.get("model") or "cached"
        cost = 0.0
        mock = bool(meta.get("mock"))
        front_status = str(meta.get("frontStatus") or "cached")
    else:
        left, front, spec_provider, spec_model, cost, mock, front_status = _acquire(
            description, style_id, stick_figure, skin, model_id, budget, allow_mock,
        )
        cache.mkdir(parents=True, exist_ok=True)
        left.save(cache / "source_left.png")
        front.save(cache / "source_front.png")
        meta = {
            "provider": spec_provider, "model": spec_model, "mock": mock,
            "frontStatus": front_status, "fingerprint": fingerprint,
            "description": description, "style": style_id, "kind": kind,
        }
        (cache / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    left_parts = _parts(left)
    front_parts = _parts(front)
    views = {
        "left": _write_view(left_parts, skin / "views" / "left", url, mirror=False),
        "right": _write_view(left_parts, skin / "views" / "right", url, mirror=True),
        "front": _write_view(front_parts, skin / "views" / "front", url, mirror=False),
    }
    _preview(skin, left_parts, front_parts)
    (skin / "rig.json").write_text(json.dumps({
        "type": "avatar", "size": list(CANVAS), "sources": list(SOURCES),
        "fingerprint": fingerprint, "mock": mock,
    }), encoding="utf-8")
    failed = [] if _opaque(left) else ["empty bust"]
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
            "score": 100 if not failed else 0,
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


def _acquire(
    description: str,
    style_id: str,
    stick_figure: bool,
    skin: Path,
    model_id: str | None,
    budget: image_models.RunBudget,
    allow_mock: bool,
) -> tuple[Image.Image, Image.Image, str, str, float, bool, str]:
    if model_id is None or not image_models.selected(model_id).available():
        if not allow_mock:
            raise RuntimeError("no image provider is configured")
        image = _seat(_mock_bust())
        return image, image.copy(), "mock", "placeholder", 0.0, True, "mock placeholder"
    spec = image_models.selected(model_id)
    client = image_models.provider(model_id)
    left_path = skin / "_gen_left.png"
    left_prompt = _prompt(description, style_id, stick_figure, "left", spec.provider)
    if not budget.allow(spec.usdPerImage):
        raise image_models.BudgetExceeded(f"{spec.id} exceeds the run cap")
    left_result = client.generate(
        left_prompt, destination=left_path, aspect_ratio="4:5", budget=budget,
    )
    left = _seat(Image.open(left_path).convert("RGBA"))
    left.save(left_path)
    cost = float(left_result.cost_usd)
    front_status = "image-model front"
    front = left
    if budget.allow(spec.usdPerImage):
        front_path = skin / "_gen_front.png"
        try:
            front_result = client.generate(
                _prompt(description, style_id, stick_figure, "front", spec.provider),
                destination=front_path, aspect_ratio="4:5", budget=budget,
                reference_path=left_path if spec.provider == "gemini" else None,
            )
            front = _seat(Image.open(front_path).convert("RGBA"))
            cost += float(front_result.cost_usd)
        except image_models.BudgetExceeded:
            front_status = "left art; budget cap"
        except Exception:
            front_status = "left art; front generation failed"
    else:
        front_status = "left art; budget cap"
    return left, front, spec.provider, left_result.model, cost, False, front_status


def _prompt(description: str, style_id: str, stick_figure: bool, view: str, provider: str) -> str:
    if stick_figure:
        subject = (
            f"A single stick-figure character: {description}. "
            "South Park construction-paper cutout, flat unshaded colors, thick black contour, "
            "simple oval eyes, no photorealism, no extra people"
        )
    else:
        subject = description
    if view == "front":
        text = f"{subject}. {rig.FRONT_PROMPT}"
    else:
        text = rig.STYLE_PROMPT.format(subject=subject, style=styles.prompt_style(style_id))
    if provider in {"together", "openrouter"}:
        return _compact(text)
    return text


def _compact(prompt: str) -> str:
    words = prompt.replace("\n", " ").split()
    kept: list[str] = []
    for word in words:
        nxt = " ".join([*kept, word])
        if len(nxt) > 380:
            break
        kept.append(word)
    return " ".join(kept)


def _mock_bust() -> Image.Image:
    """Placeholder pixels for a machine with no key. Not a drawn stick figure."""
    image = Image.new("RGBA", (320, 400), (255, 0, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle((36, 30, 150, 370), fill=(220, 40, 40, 255))
    draw.rectangle((170, 30, 284, 370), fill=(40, 40, 220, 255))
    draw.ellipse((100, 60, 220, 200), fill=(240, 210, 180, 255))
    return image


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


def _anchors(image: Image.Image) -> Anchors:
    width, height = image.size
    fallback = Anchors(
        (int(width * 0.38), int(height * 0.34)),
        (int(width * 0.62), int(height * 0.34)),
        (width // 2, int(height * 0.50)),
        int(width * 0.22),
        int(height * 0.58),
        int(height * 0.66),
        16,
    )
    try:
        from animation.parametric.factory.landmark_detector import detect_landmarks  # noqa: PLC0415

        marks = detect_landmarks(image)
        left = marks.pixel_point(marks.left_eye.center)
        right = marks.pixel_point(marks.right_eye.center)
        mouth = marks.pixel_point(marks.mouth_center)
        radius = int(max(10, min(26, round(float(marks.left_eye.iris_radius) * width))))
        chin = int(np.clip(marks.pixel_point(marks.chin_base)[1], height * 0.40, height * 0.78))
        neck = int(np.clip(max(marks.pixel_point(marks.neck_pivot)[1], chin + 10), chin + 10, height * 0.88))
        mouth_w = int(max(48, min(width * 0.34, float(marks.mouth_width) * width)))
        return Anchors(left, right, mouth, mouth_w, chin, neck, radius)
    except Exception:
        return fallback


def _skin_colour(image: Image.Image, chin: int) -> tuple[int, int, int]:
    arr = np.asarray(image.convert("RGBA"))
    band = arr[: max(1, chin)]
    opaque = band[band[:, :, 3] > 160][:, :3]
    if len(opaque) < 10:
        return (244, 210, 180)
    return tuple(int(v) for v in np.median(opaque, axis=0))


def _ellipse(size: int, fill: tuple[int, int, int, int], outline: tuple[int, int, int, int] | None = None) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((1, 1, size - 2, size - 2), fill=fill, outline=outline, width=2 if outline else 1)
    return image


def _brow(width: int, colour: tuple[int, int, int, int]) -> Image.Image:
    image = Image.new("RGBA", (width, width // 3), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.arc((1, 1, width - 2, width // 2), 200, 340, fill=colour, width=max(2, width // 14))
    return image


def _punch(image: Image.Image, centers: list[tuple[int, int]], radius: int) -> Image.Image:
    arr = np.array(image.convert("RGBA"))
    height, width = arr.shape[:2]
    yy, xx = np.ogrid[:height, :width]
    for cx, cy in centers:
        arr[((xx - cx) ** 2 + (yy - cy) ** 2) <= radius ** 2, 3] = 0
    return Image.fromarray(arr)


def _slice(image: Image.Image, y0: int, y1: int) -> tuple[Image.Image, tuple[int, int]]:
    y0, y1 = max(0, y0), min(image.height, max(y0 + 1, y1))
    return image.crop((0, y0, image.width, y1)), (0, y0)


def _parts(image: Image.Image) -> dict[str, Any]:
    anchors = _anchors(image)
    radius = anchors.radius
    punched = _punch(image, [anchors.left_eye, anchors.right_eye], radius + 2)
    head, head_xy = _slice(punched, 0, anchors.chin)
    neck, neck_xy = _slice(punched, anchors.chin, anchors.neck)
    torso, torso_xy = _slice(punched, anchors.neck, punched.height)
    skin = _skin_colour(image, anchors.chin)
    ink = (25, 22, 22, 255)
    centers = (anchors.left_eye, anchors.right_eye)

    def both(sprite: Image.Image) -> tuple[Image.Image, tuple[int, int]]:
        canvas = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
        for center in centers:
            canvas.alpha_composite(sprite, (center[0] - sprite.width // 2, center[1] - sprite.height // 2))
        box = canvas.getchannel("A").getbbox() or (0, 0, 1, 1)
        return canvas.crop(box), (box[0], box[1])

    white = _ellipse(radius * 2, (250, 250, 248, 255), ink)
    iris = _ellipse(max(6, radius), (55, 36, 28, 255))
    socket = _ellipse(radius * 2, (255, 255, 255, 255))
    lid = _ellipse(radius * 2, (*skin, 230), ink)
    closed = _ellipse(radius * 2, (*skin, 255), ink)
    brow = _brow(radius * 3, (45, 32, 28, 255))
    mouths = mouthset.place(
        (
            anchors.mouth[0] - anchors.mouth_width // 2,
            anchors.mouth[1] - anchors.mouth_width // 5,
            anchors.mouth[0] + anchors.mouth_width // 2,
            anchors.mouth[1] + anchors.mouth_width // 5,
        ),
        mouthset.draw_set(anchors.mouth_width, MOUTH_COLOURS, open_cap=anchors.mouth_width * 0.46),
    )
    expressions = mouthset.place(
        (
            anchors.mouth[0] - anchors.mouth_width // 2,
            anchors.mouth[1] - anchors.mouth_width // 5,
            anchors.mouth[0] + anchors.mouth_width // 2,
            anchors.mouth[1] + anchors.mouth_width // 5,
        ),
        mouthset.draw_expressions(anchors.mouth_width, MOUTH_COLOURS, open_cap=anchors.mouth_width * 0.46),
    )
    def at(sprite: Image.Image, center: tuple[int, int]) -> tuple[Image.Image, tuple[int, int]]:
        return sprite, (center[0] - sprite.width // 2, center[1] - sprite.height // 2)

    return {
        "torso": (torso, torso_xy),
        "neck": (neck, neck_xy),
        "head": (head, head_xy),
        "eye_white": both(white),
        "iris": both(iris),
        "eye_socket": both(socket),
        "eyes_half": both(lid),
        "eyes_closed": both(closed),
        "brow_left": at(brow, (anchors.left_eye[0], anchors.left_eye[1] - radius - 4)),
        "brow_right": at(brow, (anchors.right_eye[0], anchors.right_eye[1] - radius - 4)),
        "mouths": mouths,
        "expressions": expressions,
        "anchors": anchors,
        "size": CANVAS,
    }


def _iris_meta(parts: dict[str, Any], width: int, mirror: bool) -> list[list[int]]:
    radius = max(4, parts["anchors"].radius // 2)
    points = []
    for center in (parts["anchors"].left_eye, parts["anchors"].right_eye):
        x = width - center[0] if mirror else center[0]
        points.append([int(x), int(center[1]), radius])
    return points


def _save_layer(name: str, image: Image.Image, xy: tuple[int, int], z: int, folder: Path, url: Callable[[Path], str]) -> dict[str, Any]:
    path = folder / f"{name}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "PNG", optimize=True)
    return {"name": name, "url": url(path), "x": int(xy[0]), "y": int(xy[1]), "w": image.width, "h": image.height, "z": z}


def _write_view(parts: dict[str, Any], folder: Path, url: Callable[[Path], str], *, mirror: bool) -> dict[str, Any]:
    folder.mkdir(parents=True, exist_ok=True)
    width = CANVAS[0]

    def place(pair: tuple[Image.Image, tuple[int, int]]) -> tuple[Image.Image, tuple[int, int]]:
        image, xy = pair
        if not mirror:
            return image, xy
        return ImageOps.mirror(image), (width - xy[0] - image.width, xy[1])

    order = (
        ("torso", 0), ("neck", 1), ("head", 2), ("eye_white", 3), ("iris", 4), ("eye_socket", 4),
        ("eyes_half", 5), ("eyes_closed", 6), ("brow_left", 7), ("brow_right", 7),
    )
    layers = []
    for name, z in order:
        image, xy = place(parts[name])
        layers.append(_save_layer(name, image, xy, z, folder, url))
    visemes = {}
    for code, pair in parts["mouths"].items():
        image, xy = place(pair)
        visemes[code] = _save_layer(f"mouth_{code}", image, xy, 10, folder / "mouths", url)
    expressions = {}
    for emotion, pair in parts["expressions"].items():
        image, xy = place(pair)
        expressions[emotion] = _save_layer(f"expression_{emotion}", image, xy, 10, folder / "expressions", url)
    from .scene import EMO_BROW_POSE  # noqa: PLC0415

    emotion_brows = {}
    for emotion, pose in EMO_BROW_POSE.items():
        for side, key in (("left", "brow_left"), ("right", "brow_right")):
            image, xy = place(parts[key])
            angle = pose[2] if side == "left" else pose[3]
            turned = image.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True)
            emotion_brows[f"{emotion}_{side}"] = _save_layer(
                f"brow_{emotion}_{side}", turned, xy, 7, folder / "brows", url,
            )
    view = {
        "canvas": {"w": CANVAS[0], "h": CANVAS[1]},
        "layers": layers,
        "visemes": visemes,
        "expressions": expressions,
        "emotionBrows": emotion_brows,
        "eyes": {"open": "eye_white", "half": "eyes_half", "closed": "eyes_closed", "blink": "eyes_closed"},
        "eyeRig": {"maxShift": [10, 6], "irises": _iris_meta(parts, width, mirror)},
        "procedural": False,
        "imageModel": True,
    }
    local = json.loads(json.dumps({k: v for k, v in view.items() if k != "emotionBrows"}))
    for layer in local["layers"]:
        layer["url"] = f"{layer['name']}.png"
    for code, layer in local["visemes"].items():
        layer["url"] = f"mouths/{layer['name']}.png"
    for emotion, layer in local["expressions"].items():
        layer["url"] = f"expressions/{layer['name']}.png"
    (folder / "view.json").write_text(json.dumps(local, indent=2), encoding="utf-8")
    return view


def _preview(skin: Path, left: dict[str, Any], front: dict[str, Any]) -> None:
    def flatten(parts: dict[str, Any]) -> Image.Image:
        image = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
        for key in ("torso", "neck", "head", "eye_white", "iris", "brow_left", "brow_right"):
            sprite, xy = parts[key]
            image.alpha_composite(sprite, xy)
        image.alpha_composite(*parts["mouths"]["X"])
        return image

    flatten(left).save(skin / "character.png")
    flatten(front).save(skin / "character_front.png")


def _opaque(image: Image.Image) -> bool:
    return image.getchannel("A").getbbox() is not None


def _fingerprint(description: str, style_id: str, kind: str, model_id: str, stick_figure: bool) -> str:
    payload = json.dumps(
        {"description": description, "style": style_id, "kind": kind, "model": model_id, "stick": stick_figure, "v": 1},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:24]

