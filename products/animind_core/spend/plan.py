"""Deterministic planner. It does not call a provider."""
from __future__ import annotations

import re
from typing import Any

from products.animind_core import scene, styles

_LANG = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})?$")
_PT = re.compile(r"[áéíóúãõçàêô]|\b(?:que|não|com|para|uma|sobre)\b", re.I)


def build(body: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise ValueError("plan request must be an object")
    prompt = " ".join(str(body.get("prompt") or "").split())
    if not prompt:
        raise ValueError("prompt required")
    if len(prompt) > 1200:
        raise ValueError("prompt too long")
    language = _language(body, prompt)
    names = _names(prompt, language)
    text = prompt.lower()
    if any(word in text for word in ("debate", "versus", " vs ", "contra")):
        mode = "debate"
    elif any(word in text for word in ("diálogo", "dialogo", "dialogue", "conversa")):
        mode = "dialogue"
    else:
        mode = "monologue"
    if mode != "monologue" and len(names) < 2:
        names = [names[0], "Narrador" if language.startswith("pt") else "Narrator"]
    duration = 90 if "90" in text else 60 if "60" in text else 30
    return {
        "prompt": prompt,
        "language": language,
        "mode": mode,
        "characters": [{"name": name, "prompt": name} for name in names[:4]],
        "topic": prompt[:80],
        "tone": _choice(text, styles.TONES, styles.DEFAULT_TONE),
        "objective": _choice(text, styles.OBJECTIVES, styles.DEFAULT_OBJECTIVE),
        "artStyle": _choice(text, styles.STYLES, styles.DEFAULT),
        "characterType": "stick",
        "scenarioRequest": {"mode": "auto", "characterNames": names[:4]},
        "scenarioLabel": _scenario(text),
        "duration": duration,
        "source": "core",
        "planner": {"provider": None, "model": None, "billable": False},
    }


def _language(body: dict[str, Any], prompt: str) -> str:
    raw = body.get("language") or body.get("lang")
    if isinstance(raw, str) and _LANG.match(raw.strip()):
        return raw.strip()
    return "pt-BR" if _PT.search(prompt) else "en-US"


def _names(prompt: str, language: str) -> list[str]:
    found = re.findall(r"\b[A-ZÁÉÍÓÚÃÕÇ][\wáéíóúãõçÁÉÍÓÚÃÕÇ'-]{2,}\b", prompt)
    skip = {"The", "Uma", "Como", "Para", "Sobre", "Com"}
    names = []
    for name in found:
        if name in skip or name in names:
            continue
        names.append(name[:40])
        if len(names) == 4:
            break
    if names:
        return names
    return ["Narrador" if language.startswith("pt") else "Narrator"]


def _choice(text: str, table: dict[str, Any], default: str) -> str:
    for key in table:
        if key.lower() in text:
            return key
    return default


def _scenario(text: str) -> str:
    for name in scene.SCENARIOS:
        if name in text:
            return name
    if any(word in text for word in ("aula", "escola", "school", "class")):
        return "classroom"
    if any(word in text for word in ("espaço", "space", "estrela")):
        return "space"
    return "studio"
