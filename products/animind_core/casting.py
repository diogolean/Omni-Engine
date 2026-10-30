"""Voice casting: pick an edge-tts voice, pitch and pace for each character.

Auto mode reads the persona (gender, birth year, a few temperament words) from what
the character carries (``gender``, ``born``, ``persona``/``bio`` text) and, when online,
Wikidata (sex P21, birth P569). A user override (``voice`` on the character or
``voices[i]`` on the render request) always wins. Two speakers of the same voice are
split by pitch so a debate never sounds like one person.
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from functools import lru_cache
from typing import Any

POOL: dict[str, dict[str, list[str]]] = {
    "pt-BR": {"m": ["pt-BR-AntonioNeural"], "f": ["pt-BR-FranciscaNeural", "pt-BR-ThalitaMultilingualNeural"]},
    "en-US": {"m": ["en-US-GuyNeural", "en-US-AndrewNeural", "en-US-BrianNeural", "en-US-ChristopherNeural"],
              "f": ["en-US-JennyNeural", "en-US-AriaNeural", "en-US-EmmaNeural", "en-US-MichelleNeural"]},
}
FEMALE_WORDS = re.compile(r"\b(she|her|woman|queen|filósofa|escritora|cientista|rainha|ela|atriz|poeta(?:isa)?|pensadora|física|química)\b", re.I)
MALE_WORDS = re.compile(r"\b(he|his|man|king|filósofo|escritor|rei|ele|ator|pensador|físico|químico)\b", re.I)
FIERY = re.compile(r"\b(polêmic|provocat|furios|apaixonad|fiery|provocative|radical|revolucion|irreveren|sarcást|martelo|hammer)", re.I)
CALM = re.compile(r"\b(seren|calm|sereno|estoic|stoic|gentil|gentle|sábi|wise|ironi|paciente|patient)", re.I)
FEMALE_NAMES = {"ada", "marie", "simone", "hannah", "hipátia", "hypatia", "frida", "clarice", "cleópatra", "cleopatra",
                "rosa", "virginia", "jane", "mary", "joana", "joan", "elizabeth", "maria", "ana", "anne", "safo", "sappho",
                "lélia", "carolina", "cecília", "rachel", "florence", "emmy", "grace", "amelia", "chiquinha"}


@lru_cache(maxsize=256)
def wikidata(name: str) -> dict[str, Any]:
    """{'gender': 'm'|'f', 'born': year} from Wikidata, {} when offline or unknown."""
    if os.environ.get("ANIMIND_PERSONAS_OFFLINE") or not name.strip():
        return {}
    try:
        q = urllib.parse.urlencode({"action": "wbsearchentities", "search": name, "language": "pt", "uselang": "pt",
                                    "type": "item", "limit": 1, "format": "json"})
        hit = _get(f"https://www.wikidata.org/w/api.php?{q}")["search"]
        if not hit:
            return {}
        qid = hit[0]["id"]
        q = urllib.parse.urlencode({"action": "wbgetentities", "ids": qid, "props": "claims", "format": "json"})
        claims = _get(f"https://www.wikidata.org/w/api.php?{q}")["entities"][qid]["claims"]
        out: dict[str, Any] = {}
        sex = claims.get("P21", [{}])[0].get("mainsnak", {}).get("datavalue", {}).get("value", {}).get("id")
        if sex in ("Q6581072", "Q1052281"):
            out["gender"] = "f"
        elif sex in ("Q6581097", "Q2449503"):
            out["gender"] = "m"
        born = claims.get("P569", [{}])[0].get("mainsnak", {}).get("datavalue", {}).get("value", {}).get("time", "")
        if m := re.match(r"([+-])0*(\d+)-", born):
            out["born"] = int(m.group(2)) * (-1 if m.group(1) == "-" else 1)
        return out
    except Exception:  # noqa: BLE001 — casting falls back to heuristics
        return {}


def _get(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "animind-core/0.1 (voice casting)"})
    with urllib.request.urlopen(req, timeout=6) as r:  # noqa: S310 — fixed https host
        return json.load(r)


def traits(char: dict[str, Any]) -> dict[str, Any]:
    name = str(char.get("name") or "")
    text = " ".join(str(char.get(k) or "") for k in ("persona", "bio", "description", "role", "prompt"))
    wd = wikidata(name)
    gender = char.get("gender") or wd.get("gender")
    if gender not in ("m", "f"):
        first = name.split()[0].lower() if name.split() else ""
        f, m = len(FEMALE_WORDS.findall(text)), len(MALE_WORDS.findall(text))
        gender = "f" if first in FEMALE_NAMES or f > m else "m"
    born = char.get("born", wd.get("born"))
    temper = "fiery" if FIERY.search(text) else "calm" if CALM.search(text) else "neutral"
    age = char.get("age") or ("elder" if isinstance(born, int) and born < 1900 else "adult")
    return {"gender": gender, "born": born, "age": age, "temper": temper}


def cast(chars: list[dict[str, Any]], lang: str, overrides: list[Any] | None = None) -> list[dict[str, Any]]:
    """One voice per speaker: {'voice', 'rate', 'pitch', 'auto', 'traits'}."""
    pool = POOL.get(lang, POOL["pt-BR"])
    out: list[dict[str, Any]] = []
    for i, ch in enumerate(chars):
        ov = (overrides[i] if overrides and i < len(overrides) else None) or ch.get("voice")
        if isinstance(ov, str):
            ov = {"voice": ov}
        tr = traits(ch)
        used = [o["voice"] for o in out]
        options = pool[tr["gender"]]
        voice = next((v for v in options if v not in used), options[0])
        pitch = {"elder": -6, "adult": 0, "young": 5}.get(tr["age"], 0)
        rate = {"fiery": 6, "calm": -5, "neutral": 0}[tr["temper"]] + (-4 if tr["age"] == "elder" else 0)
        if voice in used:  # same voice twice: split by pitch
            pitch += 9 if pitch <= out[used.index(voice)]["pitchHz"] else -9
        pick = {"voice": voice, "pitchHz": pitch, "ratePct": rate, "auto": True, "traits": tr}
        if isinstance(ov, dict) and ov.get("voice"):
            pick.update(voice=str(ov["voice"]), pitchHz=int(ov.get("pitchHz", 0)), ratePct=int(ov.get("ratePct", 0)), auto=False)
        out.append(pick)
    return out


def edge_args(pick: dict[str, Any], extra_rate: int = 0) -> dict[str, str]:
    return {"rate": f"{pick['ratePct'] + extra_rate:+d}%", "pitch": f"{pick['pitchHz']:+d}Hz"}
