"""A debate script for any personas, owned by ``X-Animind-User``.

The turn order is the Aiwake debate room. This module does not rewrite that
room and does not call a paid model. With generation off, the seated brain is
a fixture stub: the room records the token ``fixture.`` and the public script
never presents that token as a character's line. A line is the speaker's own
sheet quote only when that quote is primary, on the theme, and verified (a
legacy sheet with no verification field stays speakable, the same rule as the
persona sheet).
"""
from __future__ import annotations

import json
import threading
import uuid
from typing import Any

from products.aiwake.contracts import RoomConstraints, SpeakerRole
from products.aiwake.models.base import LLMProvider, LLMResponse
from products.aiwake.room import DebateAborted, DebateRoom, Participant
from products.aiwake.settings import AiwakeSettings, MemoryConfig, ModelSpec

from . import languages, personas, script, settings

_LOCK = threading.Lock()
_DEBATES: dict[str, dict[str, Any]] = {}
_STUB = "fixture."
_LOOSE = RoomConstraints(
    max_output_chars=80,
    max_sentences=1,
    max_words=4,
    require_single_question=False,
    banned_openers=(),
    pacing_directive="",
)
_ROLES = (SpeakerRole.ORCHESTRATOR, SpeakerRole.TARGET)


class FixtureProvider(LLMProvider):
    """The only brain this endpoint seats while paid generation is off."""

    registry_name = "fixture"
    requires_api_key = False

    def __init__(self) -> None:
        super().__init__(ModelSpec(provider="fixture", model="fixture", max_tokens=32, temperature=0))

    def _dispatch(self, messages, *, max_tokens: int, temperature: float, reasoning_effort) -> LLMResponse:
        del messages, max_tokens, temperature, reasoning_effort
        return LLMResponse(text=_STUB, model="fixture", provider="fixture", finish_reason="stop")


def create(body: dict[str, Any], owner_id: str | None) -> dict[str, Any]:
    """Run one fixture debate and keep it for this owner."""
    names, theme, tag = _request(body)
    public_lang = "pt" if tag == "pt-BR" else "en"
    sheets = [personas.load(name, tag, fetch=settings.personas_online()) for name in names]
    spoken = _room(theme, len(names))
    lines = _lines(names, sheets, theme)
    debate_id = uuid.uuid4().hex[:12]
    record = {
        "id": debate_id,
        "theme": theme,
        "language": public_lang,
        "personas": names,
        "writer": "fixture",
        "llm": {"provider": "fixture", "model": "fixture", "live": False},
        "lines": lines,
        "_owner": owner_id,
        "_engine": {"room": "products.aiwake.room.DebateRoom", "provider": "fixture", "spoken": spoken},
    }
    _write(record)
    with _LOCK:
        _DEBATES[debate_id] = record
    return _public(record)


def get(debate_id: str, owner_id: str | None = None) -> dict[str, Any] | None:
    """The debate for this owner. Anyone else looks the same as a missing id."""
    with _LOCK:
        record = _DEBATES.get(debate_id)
        if record is None or record.get("_owner") != owner_id:
            return None
        return _public(record)


def _request(body: dict[str, Any]) -> tuple[list[str], str, str]:
    raw_names = body.get("personas")
    if not isinstance(raw_names, list) or not 2 <= len(raw_names) <= 6:
        raise ValueError("personas")
    names: list[str] = []
    for item in raw_names:
        if not isinstance(item, str):
            raise ValueError("personas")
        name = personas.persona_name(item)
        if not name:
            raise ValueError("personas")
        names.append(name)
    theme = body.get("theme", body.get("topic"))
    if not isinstance(theme, str) or not theme.strip():
        raise ValueError("theme")
    raw_lang = body.get("language", body.get("lang"))
    if not isinstance(raw_lang, str) or not raw_lang.strip():
        raise ValueError("language")
    tag = languages.normalize(raw_lang)
    if tag not in {"pt-BR", "en-US"}:
        raise ValueError("language")
    return names, theme.strip()[:200], tag


def _speakable(quote: dict[str, Any]) -> bool:
    """Primary, and verified when the sheet recorded a pass. Legacy sheets have no pass."""
    if not personas.is_primary(quote):
        return False
    checker = getattr(personas, "is_verified", None)
    if checker is not None:
        return bool(checker(quote))
    status = quote.get("verification")
    return status is None or status == "verified"


def _support(sheet: dict[str, Any], theme: str, previous: str) -> dict[str, Any] | None:
    pool = [
        quote for quote in sheet.get("quotes") or []
        if _speakable(quote) and script.topic_score(quote, theme) > 0
    ]
    ranked = script._rank(pool, theme, previous)
    return ranked[0] if ranked else None


def _lines(names: list[str], sheets: list[dict[str, Any]], theme: str) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    previous = ""
    for index, (name, sheet) in enumerate(zip(names, sheets)):
        quote = _support(sheet, theme, previous)
        if quote is None:
            lines.append({"speaker": index, "name": name, "text": None, "kind": "fixture", "quote": None})
            continue
        marked = script.cite(quote)
        if quote.get("verification"):
            marked["verification"] = quote["verification"]
        lines.append({
            "speaker": index,
            "name": name,
            "text": quote["text"],
            "kind": "quote",
            "quote": marked,
        })
        previous = quote["text"]
    return lines


def _room(theme: str, turns: int) -> list[str]:
    """Seat the fixture brain in the Aiwake room and take one turn per persona."""
    room_settings = AiwakeSettings(memory=MemoryConfig(persist=False))
    room = DebateRoom(room_settings, topic=theme)
    brain = FixtureProvider()
    for role in _ROLES:
        room.seat(Participant(role=role, persona_prompt="fixture", provider=brain, display_name="fixture"))
    room.open()
    spoken: list[str] = []
    try:
        for index in range(turns):
            utterance = room.speak(
                _ROLES[index % 2],
                directive=theme,
                constraints_override=_LOOSE,
                max_attempts=1,
            )
            spoken.append(utterance.text)
    except DebateAborted as exc:
        raise RuntimeError("debate") from exc
    finally:
        room.close(reason="complete")
    if spoken != [_STUB] * turns:
        raise RuntimeError("debate")
    return spoken


def _write(record: dict[str, Any]) -> None:
    """The script file lives under the owner, the same way a render job does."""
    relative = _rel(record.get("_owner"), record["id"])
    path = settings.data_dir() / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_public(record), ensure_ascii=False), encoding="utf-8")


def _rel(owner_id: str | None, debate_id: str) -> str:
    if owner_id:
        return f"users/{owner_id}/debates/{debate_id}.json"
    return f"debates/{debate_id}.json"


def _public(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if not str(key).startswith("_")}


def file_rel(owner_id: str | None, debate_id: str) -> str:
    """Relative ``ANIMIND_DATA_DIR`` path. Tests use it; the API does not echo it."""
    return _rel(owner_id, debate_id)
