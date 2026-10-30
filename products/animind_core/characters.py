"""POST /v1/characters: contract v2 layered cut-out rig.

Real path (Gemini key set), see :mod:`products.animind_core.rig`:
  Gemini image (Aiwake ``create_puppet.generate_character_image``) -> BiRefNet
  matte (rembg ``birefnet-general``, as in the Rigly rigger) -> Gemini feature
  boxes -> body / head / eyes / eyes_closed layers + 9 Rhubarb mouth sprites,
  in ``left`` and ``right`` (mirror) views. ``front`` reuses the left art.
Mock path (no key, or ``ANIMIND_FORCE_MOCK=1``): a flat PIL drawing cut into
the same layers, for wiring tests. ``generator`` says which one ran.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import rig, settings, sheet

_LOG = logging.getLogger("animind_core")
_BUILD_LOCK = threading.Lock()  # one character at a time: BiRefNet needs ~8 GB on CPU


def _slug(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return (re.sub(r"[^a-z0-9]+", "_", ascii_text.lower()).strip("_") or "character")[:32]


def puppets_dir() -> Path:
    return settings.data_dir() / "assets" / "puppets"


def load(character_id: str) -> dict[str, Any] | None:
    """Saved ``character.json`` for an id made by this core, else None."""
    if not re.fullmatch(r"[a-z0-9_]{1,48}", character_id or ""):
        return None
    path = puppets_dir() / character_id / "character.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def create_character(req: dict[str, Any], base_url: str) -> dict[str, Any]:
    source = req.get("source") or {}
    kind = source.get("kind")
    if kind == "prompt":
        subject = str(source.get("prompt") or "").strip()[:400]
        if not subject:
            raise ValueError("source.prompt is required")
    elif kind == "upload":
        if source.get("rightsConfirmed") is not True:
            raise ValueError("upload requires rightsConfirmed: true")
        raise NotImplementedError("upload source is a photo rig made by the app; the core does not cut photos yet")
    elif kind == "catalog":
        raise NotImplementedError("catalog source is a photo rig made by the app; the core does not cut photos yet")
    else:
        raise ValueError("source.kind must be prompt, catalog, or upload")

    name = (str(req.get("name") or "").strip() or subject)[:40]
    character_id = f"{_slug(name)}_{uuid.uuid4().hex[:8]}"
    assets = settings.data_dir() / "assets"
    skin = assets / "puppets" / character_id
    files_base = f"{base_url}/v1/files"

    def url(path: Path) -> str:
        return f"{files_base}/{path.relative_to(assets).as_posix()}"

    mode = "gemini" if settings.gemini_available() else "mock"
    with _BUILD_LOCK:
        if mode == "gemini":
            built = rig.build_rig(subject, skin, url, raw_prompt=req.get("rawPrompt"))
        else:
            built = rig.build_mock_rig(name, skin, url)

    character = {
        "id": character_id,
        "name": name,
        "status": "ready",
        "style": "rig",
        "views": built["views"],
        "createdAt": datetime.now(timezone.utc).isoformat(),
        # Additive fields beyond lib/contract.ts v2:
        "generator": mode,
        "viewStatus": built["viewStatus"],
        "mouthShots": built["mouthShots"],
        "imageUrl": url(skin / "character.png"),
        # Review sheet: every viseme and eye state per view, for a human check.
        "reviewSheetUrl": url(sheet.build(skin, name)),
    }
    (skin / "character.json").write_text(json.dumps(character, indent=2), encoding="utf-8")
    return character
