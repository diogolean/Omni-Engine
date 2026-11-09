"""Owner-scoped adapter to the canonical Aiwake character pipeline."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

SCHEMA = "animind.puppet.v3"
_ID = re.compile(r"^[a-z0-9_]{1,48}$")


def generate_avatar(
    body: dict[str, Any],
    owner: str,
    base_url: str,
) -> dict[str, Any]:
    """Generate one complete layered puppet without reimplementing its factory."""
    from . import characters, rig, scene, script, settings  # noqa: PLC0415
    from .admin_avatar import image_key_present, published_rates  # noqa: PLC0415
    from .spend.guard import SpendDenied, current  # noqa: PLC0415

    name = str(body.get("name") or "person")[:40]
    prompt = str(body.get("prompt") or name)[:400]
    paid = not rig.is_einstein(name, prompt)
    if paid:
        if not image_key_present():
            raise SpendDenied("credential")
        if not published_rates():
            raise SpendDenied("price_unavailable")
        if os.getenv("ANIMIND_AVATAR_SPEND_APPROVED") != "1":
            raise SpendDenied("approval")
        frame = current()
        limits = frame.limits if frame is not None else None
        if limits is None or limits.max_images < rig.MAX_IMAGE_CALLS:
            raise SpendDenied("over_cap")
        if limits.max_retries + 1 < rig.MAX_RETRIES:
            raise SpendDenied("over_cap")
    request = {
        **body,
        "name": name,
        "type": "rig",
        "source": {"kind": "prompt", "prompt": prompt},
        "reuse": body.get("reuse", False),
        "rawPrompt": body.get("rawPrompt"),
    }
    root = settings.data_dir() / "users" / owner / "puppets"
    result = characters.create_character(
        request,
        base_url,
        puppets_root=root,
    )
    failed = list((result.get("qa") or {}).get("failed") or [])
    if result.get("generator") == "mock":
        failed.append("provider_fixture")
    views = result.get("views") or {}
    for required in ("front", "left", "right", "profile"):
        if required not in views:
            failed.append(f"view_{required}_missing")
    if "mirror" in str((result.get("viewStatus") or {}).get("right") or "").lower():
        failed.append("view_right_reused_mirror")
    if result.get("frontEqualsLeft") is True:
        failed.append("view_front_reused_from_left")
    opaque_heads = _opaque_view_heads(root / str(result.get("id") or ""), views)
    failed.extend(f"view_{name}_head_opaque" for name in opaque_heads)
    alignment = _alignment(views)
    failed.extend(
        f"view_{name}_alignment"
        for name, report in alignment.items()
        if not report["pass"]
    )
    result["schema"] = SCHEMA
    result["owner"] = owner
    result["status"] = "ready" if not failed else "review_required"
    result["ready"] = not failed
    result["review_required"] = bool(failed)
    result["defects"] = sorted(set(failed))
    result["scenario"] = {"embedded": False, "control": "shared", "states": ["on", "off"]}
    result["camera"] = {
        "contract": "common-canvas-per-view",
        "canvas": {
            name: view.get("canvas")
            for name, view in views.items()
            if isinstance(view, dict)
        },
        "framing": "same eye line, head scale and shoulder frame in generated-view prompts",
    }
    result["alignment"] = alignment
    result["expressions"] = {
        name: sorted((view.get("expressions") or {}).keys())
        for name, view in views.items()
        if isinstance(view, dict)
    }
    result["expressionContract"] = {
        "emotions": list(script.EMOTIONS),
        "mouth": dict(scene.EMO_MOUTH),
        "brows": {
            emotion: list(pose)
            for emotion, pose in scene.EMO_BROW_POSE.items()
        },
        "visemes": list("XABCDEFGH"),
        "lids": ["open", "half", "closed"],
        "gaze": ["center", "left", "right", "up", "down"],
        "initial": "neutro",
    }
    result["provenance"] = {
        "generator": result.get("generator"),
        "viewStatus": result.get("viewStatus"),
        "views": {
            name: {
                "status": (result.get("viewStatus") or {}).get(name),
                "resolution": view.get("canvas"),
                "reused": any(
                    word in str((result.get("viewStatus") or {}).get(name) or "").lower()
                    for word in ("mirror", "reuse", "cached")
                ),
            }
            for name, view in views.items()
            if isinstance(view, dict)
        },
        "realImage": result.get("generator") not in {"mock", "procedural"},
        "reused": bool(
            result.get("reused")
            or result.get("cached")
            or result.get("generator") == "cached-plate"
        ),
        "frontKind": result.get("frontKind"),
        "frontEqualsLeft": result.get("frontEqualsLeft"),
        "frontIsTruePhoto": result.get("frontIsTruePhoto"),
        "opaqueHeadViews": opaque_heads,
    }
    if isinstance(result.get("qa"), dict):
        result["qa"]["costUsd"] = None
    result["stages"] = {
        "pipeline": "products.animind_core.characters.create_character",
        "actualModules": [
            "products.animind_core.characters.create_character",
            "products.animind_core.rig.build_rig",
            "products.aiwake.animator.factory.create_puppet.generate_character_image",
            "products.animind_core.rig.detect_features",
            "products.animind_core.rig.BiRefNetMatte",
            "products.animind_core.rig.assemble",
            "products.animind_core.mouthset",
            "products.animind_core.brow_draw",
            "products.animind_core.qa.finalize",
            "products.animind_core.sheet",
            "products.animind_core.eye_control.assess_skin",
        ],
        "architecturalSources": list(characters.AIWAKE_AVATAR_SOURCES),
        "qa": "products.animind_core.qa.finalize",
        "eyeControl": "products.animind_core.eye_control.assess_skin",
    }
    result["callPlan"] = rig.call_plan()
    return result


def keep(owner: str, result: dict[str, Any]) -> dict[str, Any]:
    """Persist exactly the public POST document that GET returns."""
    character_id = str(result.get("id") or "")
    if not _ID.fullmatch(character_id):
        raise ValueError("avatar id")
    path = _root(owner) / character_id / "character.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(result, indent=2), encoding="utf-8")
    temporary.replace(path)
    return result


def get(owner: str | None, character_id: str) -> dict[str, Any] | None:
    if owner is None or not _ID.fullmatch(character_id or ""):
        return None
    path = _root(owner) / character_id / "character.json"
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return found if found.get("id") == character_id else None


def _root(owner: str) -> Path:
    from . import settings  # noqa: PLC0415

    return settings.data_dir() / "users" / owner / "puppets"


def _alignment(views: dict[str, Any]) -> dict[str, Any]:
    """Geometry evidence for neck/head continuity and feature containment."""
    reports: dict[str, Any] = {}
    for view_name, view in views.items():
        if not isinstance(view, dict):
            continue
        layers = {
            str(layer.get("name")): layer
            for layer in view.get("layers") or []
            if isinstance(layer, dict)
        }
        head = _box(layers.get("head"))
        neck = _box(layers.get("neck"))
        torso = _box(layers.get("torso") or layers.get("body"))
        eyes = _box(layers.get("eyes"))
        brows = _box(layers.get("brows"))
        mouth = _box((view.get("visemes") or {}).get("X"))
        checks = {
            "torsoNeckOverlapPx2": _overlap(torso, neck),
            "neckHeadOverlapPx2": _overlap(neck, head),
            "eyesInsideHead": _center_inside(eyes, head),
            "mouthInsideHead": _center_inside(mouth, head),
            "browsInsideHead": _center_inside(brows, head),
        }
        checks["pass"] = (
            checks["torsoNeckOverlapPx2"] > 0
            and checks["neckHeadOverlapPx2"] > 0
            and checks["eyesInsideHead"]
            and checks["mouthInsideHead"]
            and checks["browsInsideHead"]
        )
        reports[view_name] = checks
    return reports


def _box(layer: Any) -> tuple[int, int, int, int] | None:
    if not isinstance(layer, dict):
        return None
    try:
        x, y, w, h = (int(layer[key]) for key in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError):
        return None
    return x, y, x + w, y + h


def _overlap(
    first: tuple[int, int, int, int] | None,
    second: tuple[int, int, int, int] | None,
) -> int:
    if first is None or second is None:
        return 0
    return max(0, min(first[2], second[2]) - max(first[0], second[0])) * max(
        0, min(first[3], second[3]) - max(first[1], second[1])
    )


def _center_inside(
    feature: tuple[int, int, int, int] | None,
    head: tuple[int, int, int, int] | None,
) -> bool:
    if feature is None or head is None:
        return False
    x = (feature[0] + feature[2]) / 2
    y = (feature[1] + feature[3]) / 2
    return head[0] <= x <= head[2] and head[1] <= y <= head[3]


def _opaque_view_heads(skin: Path, views: dict[str, Any]) -> list[str]:
    from PIL import Image  # noqa: PLC0415

    opaque: list[str] = []
    for name in views:
        path = skin / "views" / name / "head.png"
        if not path.is_file():
            continue
        with Image.open(path) as image:
            if "A" not in image.getbands() or image.getchannel("A").getextrema()[0] == 255:
                opaque.append(name)
    return opaque
