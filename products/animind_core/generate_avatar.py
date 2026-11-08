"""Public entrance to the existing Aiwake puppet factory.

``create_puppet.generate_character_image``, ``remove_background`` and
``auto_rig_character`` stay the production steps. This module only orders
them, times them, and refuses a false ready. A failed gate keeps the files.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

SCHEMA = "animind.puppet.v3"


def generate_avatar(
    body: dict[str, Any],
    owner: str,
    base_url: str,
) -> dict[str, Any]:
    """One puppet for this owner. Paid image only after the spend gates."""
    from .admin_avatar import image_key_present, published_image_rate  # noqa: PLC0415
    from .spend.guard import SpendDenied, billable  # noqa: PLC0415

    if not image_key_present():
        raise SpendDenied("credential")
    if published_image_rate() is None:
        raise SpendDenied("price_unavailable")

    name = str(body.get("name") or "person")[:40]
    prompt = str(body.get("prompt") or name)[:400]
    character_id = _character_id(name, prompt)
    skin = _skin(owner, character_id)
    skin.mkdir(parents=True, exist_ok=True)
    stages: list[dict[str, Any]] = []
    original = skin / "source_original.png"

    started = time.perf_counter()
    from products.aiwake.animator.factory.create_puppet import (  # noqa: PLC0415
        generate_character_image,
    )

    with billable(
        provider="google",
        model="gemini-2.5-flash-image",
        operation="image",
        prompt=prompt,
        images=1,
    ) as attempt:
        attempt.mark_sent()
        model = generate_character_image(prompt, original, aspect_ratio="9:16")
    stages.append({
        "id": "image",
        "seconds": round(time.perf_counter() - started, 4),
        "source": "fixture" if model == "fixture-image" else "factory",
        "model": model,
    })

    started = time.perf_counter()
    from products.aiwake.animator.factory.create_puppet import (  # noqa: PLC0415
        remove_background,
    )

    backend = remove_background(original, skin / "character.png")
    stages.append({
        "id": "cutout",
        "seconds": round(time.perf_counter() - started, 4),
        "source": backend,
    })

    started = time.perf_counter()
    from animation.parametric.factory.rigger import auto_rig_character  # noqa: PLC0415

    auto_rig_character(skin, character_id, facing="left")
    manifest = json.loads((skin / "puppet.json").read_text(encoding="utf-8"))
    stages.append({
        "id": "rig",
        "seconds": round(time.perf_counter() - started, 4),
        "source": "animation.parametric.factory.rigger.auto_rig_character",
    })
    stages.append({
        "id": "brows",
        "seconds": 0.0,
        "source": "manifest",
        "note": "parametric brow model stored on the puppet, not painted into the head plate",
    })

    defects = _defects(skin, manifest)
    stages.append({"id": "qa", "seconds": 0.0, "source": "local", "defects": defects})
    status = "ready" if not defects else "review_required"
    result = {
        "schema": SCHEMA,
        "id": character_id,
        "name": name,
        "status": status,
        "ready": status == "ready",
        "owner": owner,
        "scenario": {"embedded": False, "control": "shared", "states": ["on", "off"]},
        "anchors": manifest.get("anchors"),
        "framing": manifest.get("framing"),
        "facing": manifest.get("facing") or "left",
        "canvas": manifest.get("canvas_size"),
        "layers": sorted((manifest.get("layers") or {}).keys()),
        "brows": manifest.get("brows"),
        "stages": stages,
        "defects": defects,
        "files": {
            "source": _url(base_url, owner, character_id, "source_original.png"),
            "cutout": _url(base_url, owner, character_id, "character.png"),
            "manifest": _url(base_url, owner, character_id, "puppet.json"),
        },
    }
    (skin / "generate_avatar.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8",
    )
    public = dict(result)
    public.pop("owner", None)
    return public


def _defects(skin: Path, manifest: dict[str, Any]) -> list[str]:
    defects: list[str] = []
    layers = manifest.get("layers") or {}
    anchors = manifest.get("anchors") or {}
    if "body" not in layers:
        defects.append("body_missing")
    if "head" not in layers:
        defects.append("head_missing")
    neck = anchors.get("neck_pivot")
    chin = anchors.get("chin_base")
    if not neck or not chin or neck[1] <= chin[1]:
        defects.append("neck_junction")
    mouths = [name for name in layers if str(name).startswith("mouth_")]
    if len(mouths) < 9:
        defects.append("mouths_missing")
    if "eyes_open" not in layers:
        defects.append("eyes_missing")
    brows = manifest.get("brows") or {}
    if not brows or "angles" not in brows or "neutral" not in (brows.get("angles") or {}):
        defects.append("brows_missing")
    cut = skin / "character.png"
    if not cut.is_file():
        defects.append("cutout_missing")
    else:
        from PIL import Image  # noqa: PLC0415

        with Image.open(cut) as image:
            if "A" not in image.getbands() or image.getchannel("A").getextrema()[0] == 255:
                defects.append("alpha_opaque")
    if (skin / "bg.png").is_file():
        pass
    else:
        defects.append("scenario_layer_missing")
    return defects


def _character_id(name: str, prompt: str) -> str:
    slug = "".join(ch if ch.isalnum() else "-" for ch in name.lower()).strip("-") or "person"
    digest = hashlib.sha256(prompt.encode()).hexdigest()[:8]
    return f"{slug}-{digest}"


def _skin(owner: str, character_id: str) -> Path:
    from . import settings  # noqa: PLC0415

    return settings.data_dir() / "users" / owner / "puppets" / character_id


def _url(base_url: str, owner: str, character_id: str, name: str) -> str:
    return f"{base_url}/v1/files/users/{owner}/puppets/{character_id}/{name}"
