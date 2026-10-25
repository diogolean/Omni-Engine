# -*- coding: utf-8 -*-
"""Per-reel manifest of approved stills and the TTS chunks that match them."""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from pathlib import Path

_TTS_LOCK = threading.Lock()
_TTS_RESUMED = False


def mark_tts_resumed(value: bool) -> None:
    global _TTS_RESUMED
    with _TTS_LOCK:
        _TTS_RESUMED = bool(value)


def tts_was_resumed() -> bool:
    with _TTS_LOCK:
        return _TTS_RESUMED


def _slug(reel_id: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", (reel_id or "reel").strip())
    text = text.strip("._")[:80]
    return text or "reel"


def _script_key(script: str) -> str:
    raw = (script or "").encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def checkpoint_mode() -> str:
    """``use``, ``ignore``, or ``clear``.

    ``REEL_CHECKPOINT=0`` skips resume. ``clear`` deletes the manifest first.
    """
    raw = (os.getenv("REEL_CHECKPOINT") or "").strip().lower()
    if raw in {"0", "off", "false", "no", "ignore"}:
        return "ignore"
    if raw in {"clear", "reset"}:
        return "clear"
    return "use"


class ReelCheckpoint:
    """One manifest per reel, under the channel store. Not a temp folder."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    @classmethod
    def for_reel(cls, channel: str, reel_id: str) -> ReelCheckpoint:
        from shared.paths import channel_store_dir

        root = channel_store_dir(channel or "ancient_knowledge", create=True)
        folder = root / "reel_checkpoints" / _slug(reel_id)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "manifest.json"
        if checkpoint_mode() == "clear" and path.is_file():
            path.unlink()
        return cls(path)

    def approved_beat(self, index: int) -> dict | None:
        if checkpoint_mode() == "ignore":
            return None
        with self._lock:
            row = self._load().get("beats", {}).get(str(int(index)))
        if not isinstance(row, dict) or not row.get("approved"):
            return None
        path = Path(str(row.get("path") or ""))
        if not path.is_file():
            return None
        return dict(row)

    def save_beat(
        self,
        index: int,
        *,
        image: Path | str,
        score: float | None,
        reason: str,
        rung: str,
        verdict: dict | None,
        first_pass: bool | None,
        kind: str = "object",
    ) -> None:
        path = Path(image)
        if not path.is_file():
            return
        record = {
            "path": str(path),
            "score": score,
            "reason": reason or "",
            "rung": rung or "",
            "verdict": _jsonable(verdict or {}),
            "approved": True,
            "first_pass": first_pass,
            "kind": kind if kind in {"object", "scene"} else "object",
        }
        with self._lock:
            data = self._load()
            beats = data.setdefault("beats", {})
            beats[str(int(index))] = record
            self._write(data)

    def save_tts(
        self,
        *,
        input_script: str,
        accepted_script: str,
        narration_path: Path | str | None,
        cta_path: Path | str | None,
        voice_path: Path | str | None,
        word_timings: list | None,
        narr_dur: float,
        total_s: float,
        cta_dur: float,
        silence_s: float,
    ) -> None:
        voice = Path(voice_path) if voice_path else None
        if voice is None or not voice.is_file():
            return
        timings = []
        for row in word_timings or []:
            if isinstance(row, (list, tuple)) and len(row) >= 3:
                timings.append([str(row[0]), float(row[1]), float(row[2])])
        record = {
            "script": accepted_script or "",
            "narration_path": str(narration_path or ""),
            "cta_path": str(cta_path or ""),
            "voice_path": str(voice),
            "word_timings": timings,
            "narr_dur": float(narr_dur or 0.0),
            "total_s": float(total_s or 0.0),
            "cta_dur": float(cta_dur or 0.0),
            "silence_s": float(silence_s or 0.0),
        }
        with self._lock:
            data = self._load()
            bucket = data.setdefault("tts", {})
            bucket[_script_key(input_script)] = record
            self._write(data)

    def save_script(self, script: str) -> None:
        """Remember a narration that already passed, so a DNS blip can resume it."""
        text = (script or "").strip()
        if len(text.split()) < 40:
            return
        with self._lock:
            data = self._load()
            data["script"] = text
            self._write(data)

    def load_script(self) -> str:
        if checkpoint_mode() == "ignore":
            return ""
        with self._lock:
            text = self._load().get("script") or ""
        return str(text).strip()

    def load_tts(self, input_script: str) -> dict | None:
        if checkpoint_mode() == "ignore":
            return None
        with self._lock:
            row = self._load().get("tts", {}).get(_script_key(input_script))
        if not isinstance(row, dict):
            return None
        voice = Path(str(row.get("voice_path") or ""))
        if not voice.is_file():
            return None
        narr = Path(str(row.get("narration_path") or ""))
        if str(row.get("narration_path") or "") and not narr.is_file():
            return None
        cta_raw = str(row.get("cta_path") or "")
        cta = Path(cta_raw) if cta_raw else None
        if cta is not None and not cta.is_file():
            return None
        timings = []
        for item in row.get("word_timings") or []:
            if isinstance(item, (list, tuple)) and len(item) >= 3:
                timings.append((str(item[0]), float(item[1]), float(item[2])))
        return {
            "script": str(row.get("script") or ""),
            "voice_path": voice,
            "narration_path": narr if narr.is_file() else None,
            "cta_path": cta,
            "word_timings": timings,
            "narr_dur": float(row.get("narr_dur") or 0.0),
            "total_s": float(row.get("total_s") or 0.0),
            "cta_dur": float(row.get("cta_dur") or 0.0),
            "silence_s": float(row.get("silence_s") or 0.0),
        }

    def _load(self) -> dict:
        if not self.path.is_file():
            return {"beats": {}, "tts": {}}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"beats": {}, "tts": {}}
        if not isinstance(payload, dict):
            return {"beats": {}, "tts": {}}
        payload.setdefault("beats", {})
        payload.setdefault("tts", {})
        return payload

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)


def _jsonable(value):
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
