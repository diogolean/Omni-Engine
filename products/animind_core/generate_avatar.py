"""Owner-scoped adapter to the canonical Aiwake character pipeline."""
from __future__ import annotations

from pathlib import Path
from typing import Any

SCHEMA = "animind.puppet.v3"


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
    result["schema"] = SCHEMA
    result["status"] = "ready" if not failed else "review_required"
    result["ready"] = not failed
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
    result["stages"] = {
        "pipeline": "products.animind_core.characters.create_character",
        "reusedModules": list(characters.AIWAKE_AVATAR_SOURCES),
        "qa": "products.animind_core.qa.finalize",
        "eyeControl": "products.animind_core.eye_control.assess_skin",
    }
    result["callPlan"] = rig.call_plan()
    return result


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
