"""POST /v1/preview and POST /v1/scenarios.

The Animind app (``feat/mvp-ui``) calls both before a full rig exists.
``/v1/preview`` returns a small PNG cut-out, not a stand-in SVG.
``/v1/scenarios`` maps a cast or a prompt onto a built-in plate. The core
does not invent a new set from a free-text prompt.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any

from PIL import Image, ImageDraw

from . import scene, settings

_ACCENTS = ("hsl(218 75% 64%)", "hsl(275 67% 68%)", "hsl(163 61% 55%)", "hsl(30 80% 62%)")
_CLASSICAL = (
    "socrates", "platao", "plato", "aristoteles", "aristotle", "diogenes", "epicuro", "epicurus", "seneca",
    "marco aurelio", "marcus aurelius", "heraclito", "heraclitus", "pitagoras", "pythagoras", "parmenides",
    "zenao", "zeno", "democrito", "democritus", "tales", "thales", "protagoras", "hipocrates", "hippocrates",
    "euclides", "euclid", "arquimedes", "archimedes", "cicero", "epicteto", "epictetus", "homero", "homer",
    "safo", "sappho", "pericles", "hipatia", "hypatia", "plotino", "plotinus", "xenofonte", "anaximandro",
)


def _fold(text: str) -> str:
    stripped = unicodedata.normalize("NFD", text or "")
    stripped = "".join(ch for ch in stripped if unicodedata.category(ch) != "Mn")
    return stripped.lower()


def place_for_persona(name: str) -> str | None:
    folded = f" {_fold(name)} "
    folded = re.sub(r"[^a-z]+", " ", folded)
    folded = f" {folded.strip()} "
    for key in _CLASSICAL:
        if f" {key} " in folded:
            return "agora"
    return None


def place_for_prompt(prompt: str) -> str:
    text = _fold(prompt)
    if re.search(r"grecia|grego|romano|antiguidade|agora|templo|coluna", text):
        return "agora"
    if re.search(r"espaco|nave|planeta|estrela|futuro|lua|marte|space|starship", text):
        return "space"
    if re.search(r"escola|sala de aula|lousa|quadro|universidade|classroom", text):
        return "classroom"
    if re.search(r"arena|estadio|palco|debate|auditorio", text):
        return "arena"
    return "studio"


def resolve_scenario(body: dict[str, Any]) -> dict[str, Any]:
    """The contract the app posts to ``POST /v1/scenarios``."""
    if not isinstance(body, dict):
        raise ValueError("scenario request must be an object")
    mode = body.get("mode")
    names = body.get("characterNames")
    if mode not in {"auto", "prompt"} or not isinstance(names, list):
        raise ValueError("mode must be auto or prompt, and characterNames must be a list")
    prompt = str(body.get("prompt") or "").strip()
    if mode == "prompt" and not prompt:
        raise ValueError("prompt is required when mode is prompt")
    era = next((place for place in (place_for_persona(str(name)) for name in names) if place), None)
    if mode == "auto" and era:
        return {"scenario": era, "source": "core"}
    scenario = place_for_prompt(prompt) if mode == "prompt" else (era or "studio")
    if scenario not in scene.SCENARIOS:
        scenario = "studio"
    out: dict[str, Any] = {"scenario": scenario, "source": "core"}
    if mode == "prompt":
        out["prompt"] = prompt
        out["note"] = "Mapped to the nearest built-in set. The core does not generate a new plate from this prompt."
    return out


def _png(seed: int, kind: str, dest) -> None:
    hue = 20 + (seed % 200)
    image = Image.new("RGBA", (320, 400), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    skin = (255, 214, 186, 255)
    outfit = ((40 + hue) % 255, (80 + hue // 2) % 255, (160 + hue // 3) % 255, 255)
    draw.rounded_rectangle((90, 210, 230, 390), 40, fill=outfit)
    draw.ellipse((100, 40, 220, 180), fill=skin)
    draw.ellipse((130, 90, 150, 112), fill=(30, 30, 40, 255))
    draw.ellipse((170, 90, 190, 112), fill=(30, 30, 40, 255))
    if kind == "stick":
        draw.line((160, 180, 160, 300), fill=outfit, width=8)
        draw.line((160, 220, 80, 270), fill=outfit, width=8)
        draw.line((160, 220, 240, 270), fill=outfit, width=8)
    else:
        draw.pieslice((118, 130, 202, 175), 10, 170, fill=(90, 40, 40, 255))
    dest.parent.mkdir(parents=True, exist_ok=True)
    image.save(dest)


def build_preview(body: dict[str, Any], base_url: str) -> dict[str, Any]:
    """Lightweight PNG the app shows before ``POST /v1/characters`` finishes."""
    if not isinstance(body, dict):
        raise ValueError("preview request must be an object")
    character = body.get("character") if isinstance(body.get("character"), dict) else {}
    name = str(character.get("name") or "").strip()
    prompt = str(character.get("prompt") or "").strip()
    if not name or not prompt:
        raise ValueError("character.name and character.prompt are required")
    kind = str(body.get("characterType") or "")
    if kind not in {"stick", "illustrated"}:
        raise ValueError("characterType must be stick or illustrated")
    index = body.get("index")
    if not isinstance(index, int) or isinstance(index, bool) or index < 0:
        raise ValueError("index must be an integer >= 0")
    digest = hashlib.sha256(f"{name}\n{prompt}\n{kind}\n{index}".encode()).hexdigest()[:16]
    preview_id = f"preview_{digest}"
    path = settings.data_dir() / "previews" / f"{preview_id}.png"
    if not path.is_file():
        _png(int(digest[:8], 16), kind, path)
    return {
        "id": preview_id,
        "index": index,
        "name": name[:40],
        "kind": "stick" if kind == "stick" else "image",
        "source": "core",
        "accent": _ACCENTS[index % len(_ACCENTS)],
        "imageUrl": f"{base_url}/v1/files/previews/{preview_id}.png",
        "note": "Core preview. The full cut-out is POST /v1/characters.",
    }
