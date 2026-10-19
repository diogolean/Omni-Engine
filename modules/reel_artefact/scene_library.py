# -*- coding: utf-8 -*-
"""Cross-reel library of approved Flux landscape, sky and sea stills.

Decision (2026-10-19): grow the library from approved stills instead of
pre-generating one. Flux Schnell on DeepInfra costs about $0.002 a still, so
a 200-image pre-render ($0.40) would mostly hold scenes no script asks for;
reusing what already passed the judge costs nothing and matches real
scripts. Real photographs never enter: they stay with the artefact cache.

* Only scene stills that passed the judge are stored, with their tags
  (scene type, geography, period, prompt words).
* A Flux rung looks here first. A match needs the same scene type and a
  tag overlap of at least ``REEL_SCENE_LIBRARY_MIN_OVERLAP`` (0.34), and the
  still must not have appeared in the last ``REEL_SCENE_LIBRARY_COOLDOWN``
  reels (3) or anywhere in this reel. The copy is judged like a new still.
* Named artefacts never come from here: the library holds no objects.

Files live in ``channels_config/<channel>/store/scene_library`` (git
ignored; on the server that store is a mounted volume, so it survives a
new image). ``REEL_SCENE_LIBRARY=0`` turns it off.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_REEL_USED: set[str] = set()
_REEL_SERIAL: list[int] = [0]
_FACTORY = Path(__file__).resolve().parents[2]
_WORD = re.compile(r"[a-z][a-z'\-]{2,}")
_STOP = frozenset(
    "the and for with from this that into over under near upon your their them "
    "they what when where which while about after before between through across "
    "around still just more some every very also only than then once have been "
    "were was are not but you our its his her she photoreal documentary film "
    "grain natural colour color real location image photo shot 35mm wide".split()
)


def enabled() -> bool:
    return (os.getenv("REEL_SCENE_LIBRARY") or "1").strip().lower() not in {
        "0", "off", "false", "no",
    }


def library_dir(channel: str) -> Path:
    override = (os.getenv("REEL_SCENE_LIBRARY_DIR") or "").strip()
    if override:
        return Path(override)
    name = (channel or "ancient_knowledge").strip() or "ancient_knowledge"
    return _FACTORY / "channels_config" / name / "store" / "scene_library"


def _index_path(channel: str) -> Path:
    return library_dir(channel) / "index.json"


def _read(channel: str) -> dict:
    path = _index_path(channel)
    if not path.is_file():
        return {"version": 1, "reels": 0, "items": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "reels": 0, "items": []}
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return {"version": 1, "reels": 0, "items": []}
    return data


def _write(channel: str, data: dict) -> None:
    path = _index_path(channel)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def tags_for(*parts: str) -> list[str]:
    seen: list[str] = []
    for part in parts:
        for word in _WORD.findall((part or "").lower()):
            if word in _STOP or word in seen:
                continue
            seen.append(word)
    return seen[:40]


def _overlap(a: list[str], b: list[str]) -> float:
    left, right = set(a), set(b)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _float(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


def start_reel(channel: str = "ancient_knowledge") -> None:
    """Count one reel for the cooldown and clear this reel's used set."""
    with _LOCK:
        _REEL_USED.clear()
        if not enabled():
            return
        data = _read(channel)
        data["reels"] = int(data.get("reels") or 0) + 1
        _REEL_SERIAL[0] = data["reels"]
        try:
            _write(channel, data)
        except OSError:
            pass


def find(
    channel: str,
    *,
    scene_type: str,
    geography: str = "",
    prompt: str = "",
    period: str = "",
) -> Path | None:
    """Best stored still for this scene, or None."""
    if not enabled():
        return None
    want = tags_for(scene_type, geography, period, prompt)
    floor = _float("REEL_SCENE_LIBRARY_MIN_OVERLAP", 0.34)
    cooldown = int(_float("REEL_SCENE_LIBRARY_COOLDOWN", 3))
    with _LOCK:
        data = _read(channel)
        serial = _REEL_SERIAL[0] or int(data.get("reels") or 0)
        best: tuple[float, dict] | None = None
        for item in data["items"]:
            if (item.get("scene_type") or "") != (scene_type or ""):
                continue
            if item.get("sha") in _REEL_USED:
                continue
            last = int(item.get("last_reel") or -999)
            if serial and serial - last < cooldown and last > 0:
                continue
            score = _overlap(want, list(item.get("tags") or []))
            if score >= floor and (best is None or score > best[0]):
                best = (score, item)
        if best is None:
            return None
        path = library_dir(channel) / str(best[1].get("file") or "")
        if not path.is_file():
            return None
        _REEL_USED.add(str(best[1].get("sha")))
        best[1]["last_reel"] = serial
        best[1]["uses"] = int(best[1].get("uses") or 0) + 1
        try:
            _write(channel, data)
        except OSError:
            pass
    logger.info(
        "SCENE LIBRARY hit | scene=%s | overlap=%.2f | %s",
        scene_type, best[0], path.name,
    )
    return path


def add(
    channel: str,
    path: Path | str,
    *,
    scene_type: str,
    geography: str = "",
    prompt: str = "",
    period: str = "",
) -> Path | None:
    """Store one approved scene still. A file already stored is a no-op."""
    if not enabled():
        return None
    source = Path(path)
    if not source.is_file():
        return None
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    folder = library_dir(channel)
    with _LOCK:
        data = _read(channel)
        if any(item.get("sha") == sha for item in data["items"]):
            _REEL_USED.add(sha)
            return None
        folder.mkdir(parents=True, exist_ok=True)
        name = f"{(scene_type or 'scene')[:20]}_{sha[:12]}{source.suffix.lower() or '.png'}"
        dest = folder / name
        shutil.copyfile(source, dest)
        data["items"].append({
            "file": name,
            "sha": sha,
            "scene_type": scene_type or "",
            "geography": geography[:160],
            "period": period[:80],
            "prompt": prompt[:400],
            "tags": tags_for(scene_type, geography, period, prompt),
            "added": datetime.now(timezone.utc).isoformat(),
            "last_reel": _REEL_SERIAL[0],
            "uses": 1,
        })
        _REEL_USED.add(sha)
        _write(channel, data)
    logger.info("SCENE LIBRARY add | scene=%s | %s", scene_type, name)
    return dest


def copy_into(found: Path, dest: Path) -> Path:
    target = Path(dest)
    if target.suffix.lower() != found.suffix.lower():
        target = target.with_suffix(found.suffix.lower())
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(found, target)
    return target
