"""Voice casting: pick an edge-tts voice, pitch and pace for each character.

Auto mode reads the persona (gender, birth year, a few temperament words) from what
the character carries (``gender``, ``born``, ``persona``/``bio`` text) and, when online,
Wikidata (sex P21, birth P569). A user override (``voice`` on the character or
``voiceId`` on the character / ``voices[i]`` on the render request) always wins.

Every speaker gets a *different speaker*, not the same voice at another pitch: pt-BR has
one native male voice, so the pool adds the edge-tts Multilingual voices that read
Portuguese with a Brazilian accent (measured 2026-11-01: Whisper WER 0 on a pt-BR line,
IPA within 0.10-0.13 of Antonio and 0.52+ from pt-PT Duarte) and that a speaker-embedding
model (ECAPA) tells apart (cross-voice cosine <= 0.45 inside each pool; the same voice
on another sentence scores 0.77-0.90; Antonio at +9 Hz still scores 0.89, so a pitch split
is not a new voice). Each voice carries its measured median F0; a character's target F0
comes from age and temper, and the cast is the assignment of distinct voices with the lowest
total cost, plus a stable per-name tie-break. The result does not depend on speaker order,
and the same character in the same cast always gets the same voice. Pitch splitting is
only the last resort when a debate has more speakers than the pool has voices.
"""
from __future__ import annotations

import asyncio
import hashlib
import itertools
import json
import os
import re
import urllib.parse
import urllib.request
from functools import lru_cache
from typing import Any

from . import languages

# voice -> (label, median F0 in Hz measured on a pt-BR line with pyin). Native voices first.
VOICE_INFO: dict[str, tuple[str, float]] = {
    "pt-BR-AntonioNeural": ("Antonio", 160), "en-US-AndrewMultilingualNeural": ("Andrew", 109),
    "fr-FR-RemyMultilingualNeural": ("Rémy", 92), "en-US-BrianMultilingualNeural": ("Brian", 128),
    "en-AU-WilliamMultilingualNeural": ("William", 107),
    "pt-BR-FranciscaNeural": ("Francisca", 234), "pt-BR-ThalitaMultilingualNeural": ("Thalita", 196),
    "en-US-AvaMultilingualNeural": ("Ava", 214), "en-US-EmmaMultilingualNeural": ("Emma", 214),
    "de-DE-SeraphinaMultilingualNeural": ("Seraphina", 165),
}
POOL: dict[str, dict[str, list[str]]] = {
    "pt-BR": {"m": ["pt-BR-AntonioNeural", "en-US-AndrewMultilingualNeural", "fr-FR-RemyMultilingualNeural",
                    "en-US-BrianMultilingualNeural", "en-AU-WilliamMultilingualNeural"],
              "f": ["pt-BR-FranciscaNeural", "pt-BR-ThalitaMultilingualNeural", "en-US-AvaMultilingualNeural",
                    "en-US-EmmaMultilingualNeural", "de-DE-SeraphinaMultilingualNeural"]},
    "en-US": {"m": ["en-US-GuyNeural", "en-US-AndrewNeural", "en-US-BrianNeural", "en-US-ChristopherNeural"],
              "f": ["en-US-JennyNeural", "en-US-AriaNeural", "en-US-EmmaNeural", "en-US-MichelleNeural"]},
}
# Pairs the speaker-embedding check found closest (cross-voice cosine >= 0.5): never cast together.
CLOSE = {frozenset(("fr-FR-RemyMultilingualNeural", "en-AU-WilliamMultilingualNeural"))}
TARGET_F0 = {"m": {"elder": 100, "adult": 125, "young": 150}, "f": {"elder": 185, "adult": 205, "young": 225}}
TEMPER_F0 = {"fiery": 15, "calm": -10, "neutral": 0}
FEMALE_WORDS = re.compile(
    r"\b(she|her|woman|queen|filósofa|escritora|cientista|rainha|ela|atriz|poeta(?:isa)?|pensadora|"
    r"física|química|professora|debatedora|aluna|sábia)\b",
    re.I,
)
MALE_WORDS = re.compile(
    r"\b(he|his|man|king|filósofo|escritor|rei|ele|ator|pensador|físico|químico|"
    r"professor|debatedor|aluno|sábio)\b",
    re.I,
)
FIERY = re.compile(r"\b(polêmic|provocat|furios|apaixonad|fiery|provocative|radical|revolucion|irreveren|sarcást|martelo|hammer)", re.I)
CALM = re.compile(r"\b(seren|calm|sereno|estoic|stoic|gentil|gentle|sábi|wise|ironi|paciente|patient)", re.I)
FEMALE_NAMES = {"ada", "marie", "simone", "hannah", "hipátia", "hypatia", "frida", "clarice", "cleópatra", "cleopatra",
                "rosa", "virginia", "jane", "mary", "joana", "joan", "elizabeth", "maria", "ana", "anne", "safo", "sappho",
                "lélia", "carolina", "cecília", "rachel", "florence", "emmy", "grace", "amelia", "chiquinha",
                "alice", "beatriz", "bia", "lia", "nina", "rita", "sofia", "sophia", "helena", "laura"}
GENDER_ALIASES = {
    "m": "m", "male": "m", "masculino": "m", "man": "m",
    "f": "f", "female": "f", "feminino": "f", "woman": "f",
}
PUBLIC_GENDER = {"m": "male", "f": "female"}


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
    gender = GENDER_ALIASES.get(str(char.get("gender") or "").strip().casefold()) or wd.get("gender")
    if gender not in ("m", "f"):
        first = name.split()[0].lower() if name.split() else ""
        f, m = len(FEMALE_WORDS.findall(text)), len(MALE_WORDS.findall(text))
        gender = "f" if first in FEMALE_NAMES or f > m else "m"
    born = char.get("born", wd.get("born"))
    temper = "fiery" if FIERY.search(text) else "calm" if CALM.search(text) else "neutral"
    age = char.get("age") or ("elder" if isinstance(born, int) and born < 1900 else "adult")
    return {"gender": gender, "born": born, "age": age, "temper": temper}


def public_gender(char: dict[str, Any]) -> str:
    """Public API value inferred by the same rules used for casting."""
    return PUBLIC_GENDER[traits(char)["gender"]]


def _key(ch: dict[str, Any]) -> str:
    return re.sub(r"\s+", " ", str(ch.get("name") or "")).strip().casefold()


def _jitter(name: str, voice: str) -> float:
    """Stable 0..0.5 tie-break so two characters with the same traits still get a fixed, name-bound voice."""
    return int(hashlib.sha1(f"{name}|{voice}".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF / 2


def _cost(name: str, tr: dict[str, Any], voice: str) -> float:
    f0 = VOICE_INFO.get(voice, ("", 0.0))[1]
    if not f0:  # unmeasured pool (en-US): keep the listed order, broken by name
        return 0.0 + _jitter(name, voice)
    target = TARGET_F0[tr["gender"]].get(tr["age"], TARGET_F0[tr["gender"]]["adult"]) + TEMPER_F0[tr["temper"]]
    return abs(f0 - target) / 20 + _jitter(name, voice)


def _assign(names: list[str], trs: list[dict[str, Any]], options: list[str]) -> list[str]:
    """Distinct voices with the lowest total cost; CLOSE pairs are avoided. Order of ``names`` does not matter."""
    order = sorted(range(len(names)), key=lambda i: (names[i], i))
    best: tuple[float, tuple[str, ...]] | None = None
    for combo in itertools.permutations(options, len(order)):
        cost = sum(_cost(names[i], trs[i], v) for i, v in zip(order, combo))
        cost += 100 * sum(frozenset(p) in CLOSE for p in itertools.combinations(combo, 2))
        if best is None or cost < best[0] - 1e-9:
            best = (cost, combo)
    out = [""] * len(names)
    for i, v in zip(order, best[1] if best else ()):
        out[i] = v
    return out


@lru_cache(maxsize=32)
def voice_pool(lang: str) -> dict[str, list[str]]:
    """Native Edge voices for a BCP-47 locale, with curated built-ins first."""
    tag = languages.normalize(lang)
    if tag in POOL:
        return POOL[tag]

    async def discover() -> list[dict[str, Any]]:
        import edge_tts  # noqa: PLC0415
        return await edge_tts.list_voices()

    try:
        voices = asyncio.run(discover())
    except Exception as exc:  # noqa: BLE001 — surfaced as a useful render error
        raise ValueError(
            f"no built-in voice profile for {tag}; provide voices[] overrides or make the Edge voice catalogue available"
        ) from exc
    exact = [v for v in voices if str(v.get("Locale") or "").casefold() == tag.casefold()]
    if not exact:
        exact = [v for v in voices if str(v.get("Locale") or "").split("-")[0].casefold() == tag.split("-")[0].casefold()]
    pool = {
        "m": [str(v["ShortName"]) for v in exact if str(v.get("Gender") or "").casefold() == "male"],
        "f": [str(v["ShortName"]) for v in exact if str(v.get("Gender") or "").casefold() == "female"],
    }
    if not pool["m"] or not pool["f"]:
        raise ValueError(f"Edge has no complete male/female voice pool for language {tag}; provide voices[] overrides")
    return pool


def voice_catalog(lang: str) -> list[dict[str, str]]:
    """Available voice overrides for one requested output language."""
    tag = languages.normalize(lang)
    pool = voice_pool(tag)
    return [
        {
            "voiceId": voice,
            "label": VOICE_INFO.get(
                voice,
                (re.sub(r"^[a-z]{2}-[A-Z]{2}-|(Multilingual)?Neural$", "", voice), 0.0),
            )[0],
            "gender": PUBLIC_GENDER[gender],
            "language": tag,
        }
        for gender in ("m", "f")
        for voice in pool[gender]
    ]


def cast(chars: list[dict[str, Any]], lang: str, overrides: list[Any] | None = None) -> list[dict[str, Any]]:
    """One gender-matched voice per speaker; explicit ``voiceId`` wins."""
    trs = [traits(ch) for ch in chars]
    names = [_key(ch) or f"#{i}" for i, ch in enumerate(chars)]
    ovs: list[dict[str, Any] | None] = []
    for i, ch in enumerate(chars):
        ov = (
            (overrides[i] if overrides and i < len(overrides) else None)
            or ch.get("voiceId")
            or ch.get("voice")
        )
        if isinstance(ov, str):
            ovs.append({"voice": ov})
        elif isinstance(ov, dict):
            voice_id = ov.get("voiceId") or ov.get("voice")
            ovs.append(({**ov, "voice": voice_id} if voice_id else None))
        else:
            ovs.append(None)
    pool = voice_pool(lang) if any(o is None for o in ovs) else {"m": [], "f": []}
    taken = {o["voice"] for o in ovs if o}
    voices: list[str] = [""] * len(chars)
    for g in ("m", "f"):
        idx = [i for i, tr in enumerate(trs) if tr["gender"] == g and not ovs[i]]
        free = [v for v in pool[g] if v not in taken]
        if len(idx) > len(free):  # more speakers than voices: cycle the pool (pitch split below)
            for k, i in enumerate(sorted(idx, key=lambda i: (names[i], i))):
                voices[i] = (free or pool[g])[k % len(free or pool[g])]
        elif idx:
            for i, v in zip(idx, _assign([names[i] for i in idx], [trs[i] for i in idx], free)):
                voices[i] = v
    out: list[dict[str, Any]] = []
    for i, tr in enumerate(trs):
        pitch = {"elder": -6, "adult": 0, "young": 5}.get(tr["age"], 0)
        rate = {"fiery": 6, "calm": -5, "neutral": 0}[tr["temper"]] + (-4 if tr["age"] == "elder" else 0)
        pick = {"voice": voices[i], "pitchHz": pitch, "ratePct": rate, "auto": True, "traits": tr}
        if ovs[i]:
            ov = ovs[i]
            pick.update(voice=str(ov["voice"]), pitchHz=int(ov.get("pitchHz", 0)), ratePct=int(ov.get("ratePct", 0)), auto=False)
        out.append(pick)
    seen: dict[str, list[int]] = {}
    for i, p in enumerate(out):
        seen.setdefault(p["voice"], []).append(i)
    for same in seen.values():  # last resort only: same voice twice, split by pitch in a stable order
        for k, i in enumerate(sorted(same, key=lambda i: (names[i], i))[1:], 1):
            if out[i]["auto"]:
                out[i]["pitchHz"] += 9 * k if k % 2 else -9 * k
    for p in out:
        p["label"] = VOICE_INFO.get(p["voice"], (re.sub(r"^[a-z]{2}-[A-Z]{2}-|(Multilingual)?Neural$", "", p["voice"]),))[0]
        p["voiceId"] = p["voice"]
        p["gender"] = PUBLIC_GENDER[p["traits"]["gender"]]
        p["language"] = languages.normalize(lang)
    return out


def edge_args(pick: dict[str, Any], extra_rate: int = 0) -> dict[str, str]:
    return {"rate": f"{pick['ratePct'] + extra_rate:+d}%", "pitch": f"{pick['pitchHz']:+d}Hz"}
