# -*- coding: utf-8 -*-
"""Per-beat image source and the reel total."""
from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_COUNTS: dict[str, int] = {}


def reset() -> None:
    with _LOCK:
        _COUNTS.clear()


def note_source(source: str, entity: str, beat: int | str) -> None:
    label = (source or "flux").strip() or "flux"
    prefix = label.split(":", 1)[0]
    with _LOCK:
        _COUNTS[prefix] = _COUNTS.get(prefix, 0) + 1
    logger.info(
        "ARTEFACT_SOURCE | entity=%s | beat=%s | source=%s",
        entity or "",
        beat,
        label,
    )


def summary_line() -> str:
    with _LOCK:
        snap = dict(_COUNTS)
    return (
        "ARTEFACT_REEL | real={real} | nb-ref={nb_ref} | nb-text={nb_text} | "
        "flux={flux} | reuse={reuse}"
    ).format(
        real=snap.get("real", 0),
        nb_ref=snap.get("nb-ref", 0),
        nb_text=snap.get("nb-text", 0),
        flux=snap.get("flux", 0),
        reuse=snap.get("reuse", 0),
    )
