"""Catalog of puppets already on disk. A contact sheet is not an original.

The search is the same machine roots the Einstein plate lookup uses, plus
every sibling folder under those puppet directories. A puppet counts only
when ``rig.json`` or ``puppet.json`` is present. ``source_original.png``
counts only when its alpha is not fully opaque. An opaque
``views_contact_sheet.png``, including a 480×620 board, does not stand in
for that file.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PIL import Image

from . import rig, settings

_VIEWS = ("front", "left", "right")


def search_roots() -> list[Path]:
    """Directories that may hold a finished puppet. Missing ones stay listed."""
    roots: list[Path] = []
    seen: set[str] = set()

    def add(path: Path) -> None:
        key = str(path)
        if key in seen:
            return
        seen.add(key)
        roots.append(path)

    for candidate in rig.einstein_plate_candidates():
        add(candidate)
        add(candidate.parent)
    data = settings.data_dir()
    add(data / "assets" / "puppets")
    add(data / "puppets")
    add(data / "recovered")
    add(data / "users")
    return roots


def catalog() -> dict[str, Any]:
    """What this process can see. Nothing here calls a provider."""
    searched = []
    puppets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for root in search_roots():
        exists = root.is_dir()
        searched.append({"path": str(root), "exists": exists})
        if not exists:
            continue
        folders = [root]
        if root.name != "users":
            folders.extend(path for path in root.iterdir() if path.is_dir())
        else:
            folders.extend(path for path in root.glob("*/*/*") if path.is_dir())
        for folder in folders:
            if (folder / "manifest.json").is_file():
                continue
            key = str(folder.resolve()) if folder.exists() else str(folder)
            if key in seen:
                continue
            seen.add(key)
            row = _puppet(folder)
            if row is not None:
                puppets.append(row)
    for folder in _recovered_folders():
        row = _recovered(folder)
        if row is not None:
            puppets.append(row)
    return {
        "searched": searched,
        "puppets": puppets,
        "recovered": len(puppets),
        "scenario": {"embedded": False, "control": "shared", "states": ["on", "off"]},
    }


def _recovered_folders() -> list[Path]:
    root = settings.data_dir() / "recovered"
    if not root.is_dir():
        return []
    return [path for path in root.iterdir() if path.is_dir()]


def _recovered(folder: Path) -> dict[str, Any] | None:
    manifest = _read(folder / "manifest.json")
    if not manifest:
        return None
    proofs = []
    for item in manifest.get("heads") or []:
        path = folder / str(item)
        if not path.is_file():
            proofs.append({"file": str(item), "present": False})
            continue
        proofs.append({
            "file": str(item),
            "present": True,
            "sha256": _sha(path),
            "size": _size(path),
            "transparent": _has_transparency(path) is True,
            "role": "head",
        })
    original = folder / "source_original.png"
    return {
        "id": str(manifest.get("id") or folder.name),
        "family": manifest.get("family") or "unknown",
        "artStyle": manifest.get("artStyle") or "unknown",
        "storedArtStyle": manifest.get("storedArtStyle"),
        "approved": manifest.get("approved"),
        "sourceOriginal": {
            "present": original.is_file() and _has_transparency(original) is True,
            "transparent": _has_transparency(original) is True,
            "role": "head" if manifest.get("family") == "charge" else "plate",
        },
        "heads": proofs,
        "body": manifest.get("body") or "absent",
        "profile": manifest.get("profile") or "absent",
        "missing": manifest.get("missing") or [],
        "views": manifest.get("views") or {},
        "qa": manifest.get("qa"),
        "ready": bool(manifest.get("ready")),
        "scenario": {"embedded": False},
    }


def _sha(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _puppet(folder: Path) -> dict[str, Any] | None:
    rig_doc = folder / "rig.json"
    puppet_doc = folder / "puppet.json"
    if not rig_doc.is_file() and not puppet_doc.is_file():
        return None
    character = _read(folder / "character.json")
    original = folder / "source_original.png"
    transparent = _has_transparency(original)
    sheet = folder / "views_contact_sheet.png"
    views = {
        name: (folder / "views" / name / "view.json").is_file()
        for name in _VIEWS
    }
    return {
        "id": str((character or {}).get("id") or folder.name),
        "artStyle": (character or {}).get("artStyle") or "unknown",
        "sourceOriginal": {
            "present": original.is_file() and original.stat().st_size > 0,
            "transparent": transparent is True,
        },
        "contactSheet": {
            "present": sheet.is_file() and sheet.stat().st_size > 0,
            "size": _size(sheet),
            "provesOriginal": False,
        },
        "views": views,
        "rig": rig_doc.is_file(),
        "puppet": puppet_doc.is_file(),
        "ready": rig_doc.is_file() and transparent is True and all(views.values()),
    }


def _has_transparency(path: Path) -> bool | None:
    if not path.is_file() or path.stat().st_size <= 0:
        return None
    with Image.open(path) as image:
        if "A" not in image.getbands():
            return False
        low, _high = image.getchannel("A").getextrema()
    return bool(low < 255)


def _size(path: Path) -> list[int] | None:
    if not path.is_file():
        return None
    with Image.open(path) as image:
        return [image.size[0], image.size[1]]


def _read(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None
