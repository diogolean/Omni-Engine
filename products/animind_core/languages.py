"""Language profiles shared by writing, TTS, lip sync, and on-screen copy.

``language`` accepts every BCP-47 locale exposed by Edge TTS. English and
Brazilian Portuguese have rich offline template banks. Other locales use a
language-neutral script shape made only from the caller's localized topic, so
the spoken script and captions never silently fall back to another language.

Rhubarb's PocketSphinx recognizer is English-only. Non-English speech uses the
language-independent phonetic recognizer; the phase-2 measurement artifact
records both recognizers on the same edge-tts clips.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Profile:
    tag: str
    script: str
    rhubarb: str
    photo: str
    talk: str
    lesson: str
    end: str


PROFILES: dict[str, Profile] = {
    "en-US": Profile("en-US", "en-US", "pocketSphinx", "Photo", "Talk", "Lesson", "THE END"),
    "pt-BR": Profile("pt-BR", "pt-BR", "phonetic", "Foto", "Fala", "Aula", "FIM"),
}
ALIASES = {
    "en": "en-US", "en-us": "en-US", "english": "en-US",
    "pt": "pt-BR", "pt-br": "pt-BR", "portuguese": "pt-BR", "português": "pt-BR",
}


def normalize(value: object | None) -> str:
    """Canonicalize a BCP-47-ish value without closing the set of languages."""
    raw = str(value or "").strip().replace("_", "-")
    if not raw:
        return "en-US"
    if raw.casefold() in ALIASES:
        return ALIASES[raw.casefold()]
    bits = raw.split("-")
    return "-".join([bits[0].lower(), *[b.upper() if len(b) in (2, 3) else b.title() for b in bits[1:]]])


def profile(value: object | None) -> Profile:
    tag = normalize(value)
    if tag in PROFILES:
        return PROFILES[tag]
    # No English UI fallback: generic locales keep the selected language and
    # omit labels for which this package has no vetted translation.
    recognizer = "pocketSphinx" if tag.split("-")[0] == "en" else "phonetic"
    return Profile(tag, tag, recognizer, "", "", "", "")


def requested(body: dict) -> str:
    """Canonical field first; ``lang`` remains a backwards-compatible alias."""
    return normalize(body.get("language") if body.get("language") is not None else body.get("lang"))


def recognizer(value: object | None) -> str:
    return profile(value).rhubarb


__all__ = ["ALIASES", "PROFILES", "Profile", "normalize", "profile", "recognizer", "requested"]
