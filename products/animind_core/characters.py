"""POST /v1/characters: contract v2 layered cut-out rig.

Real path (Gemini key set), see :mod:`products.animind_core.rig`:
  Gemini image (Aiwake ``create_puppet.generate_character_image``) -> BiRefNet
  matte (rembg ``birefnet-general``, as in the Rigly rigger) -> Gemini feature
  boxes -> torso / neck / mouthless head / eyes / lids / brows layers + 9 Rhubarb mouth sprites,
  in ``left`` and ``right`` (mirror) views. ``front`` reuses the left art.
Mock path (no key, or ``ANIMIND_FORCE_MOCK=1``): a flat PIL drawing cut into
the same layers, for wiring tests. ``generator`` says which one ran.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import threading
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import rig, settings, sheet, styles

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


REUSE_MIN_SCORE = 80


def reusable(name: str, art_style: str, base_url: str) -> dict[str, Any] | None:
    """A saved, QA-approved Gemini rig of the same persona and art style, or None.

    Asking for "Sócrates" again must not pay for a new image: the best-scored ready rig with the same
    name (accent/case-insensitive) and style is returned, file URLs rebased on this server. Ties go to
    the newest. ``reuse: false`` in the request (or a ``rawPrompt``) always generates.
    """
    best: tuple[int, str, dict[str, Any]] | None = None
    root = puppets_dir()
    for path in root.glob("*/character.json") if root.is_dir() else []:
        try:
            c = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        score = int((c.get("qa") or {}).get("score") or 0)
        if (c.get("status") != "ready" or c.get("generator") != "gemini" or score < REUSE_MIN_SCORE
                or c.get("artStyle") != art_style or _slug(str(c.get("name") or "")) != _slug(name)
                or c.get("id") != path.parent.name or not (path.parent / "rig.json").is_file()):
            continue
        key = (score, str(c.get("createdAt") or ""), c)
        if best is None or key[:2] > best[:2]:
            best = key
    if best is None:
        return None
    text = re.sub(r"https?://[^\"\s]+?/v1/files/", f"{base_url}/v1/files/", json.dumps(best[2]))
    return json.loads(text) | {"reused": True}


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

    art_style = styles.pick(req.get("artStyle"))
    if req.get("reuse", True) is not False and not req.get("rawPrompt"):
        found = reusable(name, art_style, base_url)
        if found:
            _LOG.info("character %s reused for %r (no image generated)", found["id"], name)
            return found
    mode = "gemini" if settings.gemini_available() else "mock"
    note = None if mode == "gemini" else _mock_note()
    with _BUILD_LOCK:
        built = None
        if mode == "gemini":
            try:
                built = rig.build_rig(subject, skin, url, raw_prompt=req.get("rawPrompt"), style=art_style)
            except Exception as exc:  # noqa: BLE001
                if not settings.is_billing_error(exc):
                    raise
                # No credit / bad key: never a 500. Draw the mock rig and say why, so the app can show it.
                _LOG.warning("character %s: Gemini unavailable (%s); mock art instead", character_id, str(exc)[:160])
                shutil.rmtree(skin, ignore_errors=True)
                mode, note = "mock", f"Gemini indisponível: {str(exc)[:200]}"
        if built is None:
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
        # Why the art is mock (no key, ANIMIND_FORCE_MOCK, or Gemini 402/depleted credit); None when Gemini drew it.
        "generatorNote": note,
        "viewStatus": built["viewStatus"],
        "mouthShots": built["mouthShots"],
        "artStyle": art_style,
        "frontShot": built.get("frontShot", "mock"),
        # generate-then-verify QA: score 0-100, Gemini cost, attempts, failed gates; sheet stored with the character
        "qa": built.get("qa"),
        "qaSheetUrl": url(skin / "qa_sheet.png") if (skin / "qa_sheet.png").exists() else None,
        "frontImageUrl": url(skin / "character_front.png") if (skin / "character_front.png").exists() else None,
        "imageUrl": url(skin / "character.png"),
        # Review sheet: every viseme and eye state per view, for a human check.
        "reviewSheetUrl": url(sheet.build(skin, name, art_style)),
    }
    (skin / "character.json").write_text(json.dumps(character, indent=2), encoding="utf-8")
    return character


def _mock_note() -> str:
    if settings.gemini_dead_reason():
        return f"Gemini indisponível: {settings.gemini_dead_reason()[:200]}"
    return "sem GEMINI_API_KEY (ou ANIMIND_FORCE_MOCK=1): arte mock"


def regenerate_front(character_id: str, base_url: str) -> dict[str, Any] | None:
    """Redraw the front view of a Gemini character (frontality check + one retry) and save it."""
    saved = load(character_id)
    if not saved:
        return None
    if saved.get("generator") != "gemini" or not settings.gemini_available():
        raise NotImplementedError("front regeneration needs a Gemini character and GEMINI_API_KEY")
    assets = settings.data_dir() / "assets"
    skin = puppets_dir() / character_id

    def url(path: Path) -> str:
        return f"{base_url}/v1/files/{path.relative_to(assets).as_posix()}"

    with _BUILD_LOCK:
        built = rig.regenerate_front(skin, url)
    saved["frontShot"] = built["frontShot"]
    if "views" in built:
        saved["views"]["front"] = built["views"]["front"]
        saved["viewStatus"]["front"] = "ready (gemini front view, regenerated)"
        saved["frontImageUrl"] = url(skin / "character_front.png")
        saved["reviewSheetUrl"] = url(sheet.build(skin, saved["name"], saved.get("artStyle", "cartoon")))
    (skin / "character.json").write_text(json.dumps(saved, indent=2), encoding="utf-8")
    return saved


def rerig(character_id: str, base_url: str) -> dict[str, Any] | None:
    """Rebuild a saved character on the current layered rig from its stored shots (no image API)."""
    saved = load(character_id)
    if not saved:
        return None
    assets = settings.data_dir() / "assets"
    skin = puppets_dir() / character_id

    def url(path: Path) -> str:
        return f"{base_url}/v1/files/{path.relative_to(assets).as_posix()}"

    with _BUILD_LOCK:
        built = rig.rebuild_rig(skin, url)
    saved["views"], saved["qa"] = built["views"], built["qa"]
    saved["viewStatus"] = built["viewStatus"]
    saved["style"] = "rig"
    if (skin / "qa_sheet.png").exists():
        saved["qaSheetUrl"] = url(skin / "qa_sheet.png")
    saved["reviewSheetUrl"] = url(sheet.build(skin, saved.get("name", character_id), saved.get("artStyle", "cartoon")))
    (skin / "character.json").write_text(json.dumps(saved, indent=2), encoding="utf-8")
    return saved
