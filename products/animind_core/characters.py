"""POST /v1/characters: wrap the existing Aiwake one-click factory.

Real path (GEMINI_API_KEY set), all existing Aiwake code:
  1. ``OneShotPuppetFactory.create_character`` (products/aiwake/animator/factory/
     one_shot_factory.py): Gemini image (``create_puppet.generate_character_image``)
     -> BiRefNet cutout -> jaw slice -> ``auto_rig_character`` (eyes, mouths A..X).
  2. ``OneShotPuppetFactory.generate_view(id, "facing_right")`` and
     ``generate_view(id, "facing_front")`` for the other two views.
Mock path (no key): flat PIL shapes with the same layer names, for wiring tests.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from . import settings

_LOG = logging.getLogger("animind_core")
_FACTORY_LOCK = threading.Lock()  # ASSETS_PATH is process-global

# Rhubarb shapes -> the app's 5 visemes (A=MBP closed, C=EH, D=AA, E=AO, X=rest).
VISEME_MAP = {"mouth_rest": "X", "mouth_A": "D", "mouth_E": "C", "mouth_O": "E", "mouth_M": "A"}
RHUBARB = ("A", "B", "C", "D", "E", "F", "G", "H", "X")
VIEWS = ("facing_left", "facing_right", "facing_front")

DEFAULT_STYLE_PROMPT = (
    "Studio Ghibli character design, vintage 90s anime style, 2d cel animation, clean flat shading. "
    "Medium bust shot of {subject}, facing 25 degrees to the left, closed mouth, eyes open. "
    "Broad shoulders reaching the bottom edge. Solid pure magenta #FF00FF background. --ar 9:16"
)


def _slug(text: str) -> str:
    return (re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "character")[:32]


def _layer(name: str, rel: str, size: tuple[int, int], z: int, base_url: str) -> dict[str, Any]:
    return {"name": name, "url": f"{base_url}/{rel}", "x": 0, "y": 0, "w": size[0], "h": size[1], "z": z}


def _view_layers(skin: Path, view_dir: Path, base_url: str, rel_root: str) -> dict[str, Any]:
    """Contract layers for one view. Missing files are skipped, never invented."""
    def pick(*cands: Path) -> Path | None:
        return next((p for p in cands if p.is_file()), None)

    def rel(p: Path) -> str:
        return f"{rel_root}/{p.relative_to(skin).as_posix()}"

    order: list[tuple[str, Path | None]] = [
        ("body", pick(view_dir / "body.png", skin / "body.png")),
        ("head", pick(view_dir / "head.png", skin / "head.png")),
        ("brows", pick(view_dir / "brows.png", skin / "brows.png")),
        ("eyes", pick(view_dir / "eyes_open.png", skin / "eyes_open.png")),
        ("eyes_closed", pick(view_dir / "eyes_blink.png", skin / "eyes_blink.png")),
    ]
    for app_name, rh in VISEME_MAP.items():
        order.append((app_name, pick(view_dir / "mouths" / f"mouth_{rh}.png", skin / "mouths" / f"mouth_{rh}.png")))
    layers, canvas = [], None
    for z, (name, path) in enumerate(order):
        if path is None:
            continue
        with Image.open(path) as im:
            size = im.size
        canvas = canvas or size
        layers.append(_layer(name, rel(path), size, z, base_url))
    visemes = {}
    for rh in RHUBARB:
        p = pick(view_dir / "mouths" / f"mouth_{rh}.png", skin / "mouths" / f"mouth_{rh}.png")
        if p:
            visemes[rh] = f"{base_url}/{rel(p)}"
    return {"layers": layers, "visemes": visemes, "canvas": {"w": canvas[0], "h": canvas[1]} if canvas else None}


def _run_factory(character_id: str, prompt: str, assets: Path) -> dict[str, str]:
    """Run the existing Aiwake factory. Returns per-view status."""
    os.environ["ASSETS_PATH"] = str(assets)
    # One request is one "run" for the shared Gemini guardrail: the $0.05
    # ceiling applies per character, not per server lifetime. The 3 views cost
    # ~$0.12, so they need the operator opt-in GOOGLE_COST_ALLOW_ABOVE_CEILING=1.
    from production.cost.google_guardrail import reset_google_cost_tracker  # noqa: PLC0415

    reset_google_cost_tracker()
    from products.aiwake.animator.factory.one_shot_factory import OneShotPuppetFactory  # noqa: PLC0415

    factory = OneShotPuppetFactory()
    factory.create_character(character_id, prompt, facing="left", bg_contrast="magenta")
    status = {"facing_left": "ok"}
    for view in ("facing_right", "facing_front"):
        try:
            factory.generate_view(character_id, view)
            status[view] = "ok"
        except BaseException as exc:  # noqa: BLE001 — SystemExit included; left view stays usable
            _LOG.warning("view %s failed: %s", view, exc)
            status[view] = f"failed: {exc}"[:300]
    return status


def _mock_skin(skin: Path, name: str) -> dict[str, str]:
    """Same layer names as the rig, drawn with PIL. No network."""
    w, h = 768, 1344
    skin.mkdir(parents=True, exist_ok=True)
    (skin / "mouths").mkdir(exist_ok=True)

    def canvas() -> tuple[Image.Image, ImageDraw.ImageDraw]:
        im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        return im, ImageDraw.Draw(im)

    im, d = canvas(); d.rounded_rectangle((134, 760, 634, h), 120, fill=(70, 90, 140, 255)); im.save(skin / "body.png")
    im, d = canvas(); d.ellipse((209, 300, 559, 720), fill=(240, 200, 160, 255)); im.save(skin / "head.png")
    im, d = canvas(); d.line((290, 450, 360, 440), fill=(40, 30, 20, 255), width=10); d.line((410, 440, 480, 450), fill=(40, 30, 20, 255), width=10); im.save(skin / "brows.png")
    im, d = canvas(); [d.ellipse((x - 22, 480, x + 22, 524), fill=(30, 30, 40, 255)) for x in (325, 445)]; im.save(skin / "eyes_open.png")
    im, d = canvas(); [d.line((x - 24, 502, x + 24, 502), fill=(30, 30, 40, 255), width=6) for x in (325, 445)]; im.save(skin / "eyes_blink.png")
    opens = {"A": 2, "B": 10, "C": 22, "D": 40, "E": 30, "F": 18, "G": 8, "H": 26, "X": 4}
    for rh, o in opens.items():
        im, d = canvas(); d.ellipse((334, 610 - o // 2, 434, 610 + o // 2 + 4), fill=(120, 30, 40, 255)); im.save(skin / "mouths" / f"mouth_{rh}.png")
    (skin / "puppet.json").write_text(json.dumps({"character_id": skin.name, "skin_version": "mock", "name": name, "canvas_size": [w, h]}), encoding="utf-8")
    return {"facing_left": "mock", "facing_right": "mock (same art)", "facing_front": "mock (same art)"}


def create_character(req: dict[str, Any], base_url: str) -> dict[str, Any]:
    source = req.get("source") or {}
    kind = source.get("kind")
    if kind == "prompt":
        subject = str(source.get("prompt") or "").strip()
        if not subject:
            raise ValueError("source.prompt is required")
        name = subject[:40]
    elif kind == "upload":
        if source.get("rightsConfirmed") is not True:
            raise ValueError("upload requires rightsConfirmed: true")
        raise NotImplementedError("upload source is not wired in core v1 yet")
    elif kind == "catalog":
        raise NotImplementedError("catalog source is not wired in core v1 yet")
    else:
        raise ValueError("source.kind must be prompt, catalog, or upload")

    character_id = f"{_slug(name)}_{uuid.uuid4().hex[:8]}"
    assets = settings.data_dir() / "assets"
    skin = assets / "puppets" / character_id
    mode = "gemini" if settings.gemini_available() else "mock"
    with _FACTORY_LOCK:
        if mode == "gemini":
            prompt = req.get("rawPrompt") or DEFAULT_STYLE_PROMPT.format(subject=subject)
            status = _run_factory(character_id, prompt, assets)
        else:
            status = _mock_skin(skin, name)

    files_base = f"{base_url}/v1/files"
    rel_root = f"puppets/{character_id}"
    views = {}
    for view in VIEWS:
        view_dir = skin / "views" / view
        if mode == "gemini" and view != "facing_left" and not status.get(view, "").startswith("ok"):
            continue
        views[view] = {**_view_layers(skin, view_dir if view_dir.is_dir() else skin, files_base, rel_root), "status": status.get(view)}
    left = views["facing_left"]
    character = {
        "id": character_id,
        "name": name,
        "style": req.get("style") or "cartoon",
        "canvas": left["canvas"],
        "layers": left["layers"],
        "createdAt": datetime.now(timezone.utc).isoformat(),
        # Additive fields beyond lib/contract.ts v1:
        "views": views,
        "visemeMap": VISEME_MAP,
        "manifestUrl": f"{files_base}/{rel_root}/puppet.json",
        "generator": mode,
    }
    (skin / "character.json").write_text(json.dumps(character, indent=2), encoding="utf-8")
    return character
