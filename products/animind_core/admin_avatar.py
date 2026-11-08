"""Admin-only entrance to the Aiwake character factory.

The factory is ``rig.build_rig``, which calls
``create_puppet.generate_character_image``. A normal account never reaches
it. A trusted admin still stops before the socket when the Gemini key or
the published image rate is missing.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .spend.guard import SpendDenied
from .spend.prices import lookup

FACTORY_MODEL = "gemini-2.5-flash-image"
IMAGE_CALLS = 4  # master, mouthless head, parts sheet, side view inside build_rig


def image_key_present() -> bool:
    return bool((os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip())


def published_image_rate() -> Any:
    return lookup("google", FACTORY_MODEL)


def run(body: dict[str, Any], owner: str, base_url: str) -> dict[str, Any]:
    """One admin character. Refuses before ``build_rig`` when a gate is shut."""
    if not image_key_present():
        raise SpendDenied("credential")
    if published_image_rate() is None:
        raise SpendDenied("price_unavailable")
    from . import rig  # noqa: PLC0415

    name = str(body.get("name") or "person")[:40]
    prompt = str(body.get("prompt") or name)[:400]
    skin = Path(body.get("_skin") or _skin(owner, name))
    skin.mkdir(parents=True, exist_ok=True)

    def url(path: Path) -> str:
        root = skin.parents[2]
        try:
            relative = path.resolve().relative_to(root.resolve())
        except ValueError:
            relative = path.name
        return f"{base_url}/v1/files/{relative.as_posix()}"

    built = rig.build_rig(prompt, skin, url, style=str(body.get("artStyle") or "cartoon"))
    return built


def _skin(owner: str, name: str) -> Path:
    from . import settings  # noqa: PLC0415

    slug = "".join(ch if ch.isalnum() else "-" for ch in name.lower()).strip("-") or "person"
    return settings.data_dir() / "users" / owner / "avatars" / slug
