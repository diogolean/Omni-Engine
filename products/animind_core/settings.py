"""Runtime settings, read from the environment only."""
from __future__ import annotations

import os
from pathlib import Path


def data_dir() -> Path:
    root = Path(os.getenv("ANIMIND_DATA_DIR") or Path.home() / ".animind-core")
    root.mkdir(parents=True, exist_ok=True)
    return root


def api_key() -> str:
    return (os.getenv("CORE_API_KEY") or "").strip()


def gemini_available() -> bool:
    if (os.getenv("ANIMIND_FORCE_MOCK") or "").strip().lower() in {"1", "true", "yes"}:
        return False
    return bool((os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip())
