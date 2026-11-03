# -*- coding: utf-8 -*-
"""One cost ledger for every paid call in a reel: LLM text, vision judges, Gemini.

Stills keep their own budget in ``modules.reel_artefact.budget``. Both share
one hard cap per reel, ``REEL_REEL_BUDGET_USD`` (default $0.05): a paid LLM
call that would pass it raises :class:`SpendCapExceeded` before the request
is sent. ``budget.reset_spend`` clears the reel part; the run part keeps
growing so a batch can report its total. ``write_ledger`` dumps both to JSON.

Usage on a flat subscription (ElevenLabs on the Pro plan) is logged with
:func:`record_informational`: it shows in the ledger with its list-price
equivalent but costs nothing at the margin and never counts toward the cap.
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)

DEFAULT_REEL_CAP_USD: float = 0.05

_LOCK = threading.Lock()
_REEL: list["SpendRecord"] = []
_RUN: list["SpendRecord"] = []
_INFO: list["SpendRecord"] = []
_ATEXIT = False
# None: the reel cap is the only check. A number is the hard cap for one pin.
_PIN_CAP: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "pin_spend_cap_usd", default=None
)


class SpendCapExceeded(RuntimeError):
    """A paid call would pass the per-reel hard cap. No request was sent."""


@dataclass
class SpendRecord:
    ts: str
    provider: str
    model: str
    source: str
    kind: str
    input_tokens: int
    output_tokens: int
    images: int
    cost_usd: float
    status: str = "ok"


def pin_cap_usd() -> float | None:
    """Active per-pin cap, or None when this call is not inside a pin."""
    return _PIN_CAP.get()


@contextmanager
def pin_cap_scope(cap_usd: float | None) -> Iterator[None]:
    """Apply a per-pin cap until the block exits.

    ``None`` leaves the surrounding cap untouched. ``0`` refuses every
    estimate above zero before the request is sent.
    """
    if cap_usd is None:
        yield
        return
    token = _PIN_CAP.set(max(0.0, float(cap_usd)))
    try:
        yield
    finally:
        _PIN_CAP.reset(token)


def reel_cap_usd() -> float:
    raw = (os.getenv("REEL_REEL_BUDGET_USD") or "").strip()
    try:
        return max(0.0, float(raw)) if raw else DEFAULT_REEL_CAP_USD
    except ValueError:
        return DEFAULT_REEL_CAP_USD


def _stills_spent() -> float:
    try:
        from production.formats.economic_reel.artefact.budget import reel_spent

        return float(reel_spent())
    except Exception:  # noqa: BLE001
        return 0.0


def llm_spent() -> float:
    with _LOCK:
        return sum(r.cost_usd for r in _REEL)


def run_records() -> list[SpendRecord]:
    """Every paid call this process made, oldest first (a copy)."""
    with _LOCK:
        return list(_RUN)


def reel_total() -> float:
    """LLM calls plus generated stills for the current reel."""
    return llm_spent() + _stills_spent()


def run_total() -> float:
    with _LOCK:
        return sum(r.cost_usd for r in _RUN)


def preflight(estimate_usd: float, *, provider: str, model: str, source: str = "") -> None:
    """Raise before the request when it would pass the pin cap or the reel cap."""
    extra = max(0.0, float(estimate_usd or 0.0))
    pin_cap = _PIN_CAP.get()
    if pin_cap is not None and extra > pin_cap + 1e-12:
        msg = (
            f"PIN SPEND CAP | provider={provider} | model={model} | src={source} | "
            f"next=${extra:.5f} | cap=${pin_cap:.3f} | no request sent"
        )
        logger.error(msg)
        raise SpendCapExceeded(msg)
    cap = reel_cap_usd()
    current = reel_total()
    if current + extra > cap + 1e-12:
        msg = (
            f"REEL SPEND CAP | provider={provider} | model={model} | src={source} | "
            f"spent=${current:.5f} | next=${extra:.5f} | cap=${cap:.3f} | no request sent"
        )
        logger.error(msg)
        raise SpendCapExceeded(msg)


def record(
    *,
    provider: str,
    model: str,
    cost_usd: float,
    source: str = "",
    kind: str = "text",
    input_tokens: int = 0,
    output_tokens: int = 0,
    images: int = 0,
    status: str = "ok",
) -> SpendRecord:
    rec = SpendRecord(
        ts=datetime.now(timezone.utc).isoformat(),
        provider=str(provider or "unknown"),
        model=str(model or "unknown"),
        source=str(source or ""),
        kind=str(kind or "text"),
        input_tokens=max(0, int(input_tokens or 0)),
        output_tokens=max(0, int(output_tokens or 0)),
        images=max(0, int(images or 0)),
        cost_usd=round(max(0.0, float(cost_usd or 0.0)), 8),
        status=status,
    )
    with _LOCK:
        _REEL.append(rec)
        _RUN.append(rec)
    _register_exit_dump()
    logger.info(
        "SPEND | provider=%s | model=%s | src=%s | kind=%s | in=%d | out=%d | "
        "cost=$%.6f | reel_llm=$%.5f | cap=$%.3f",
        rec.provider, rec.model, rec.source, rec.kind, rec.input_tokens,
        rec.output_tokens, rec.cost_usd, llm_spent(), reel_cap_usd(),
    )
    return rec


def subscription_list_rate(provider: str) -> float:
    """List price per 1k characters, shown only for scale. Env overrides it."""
    raw = (os.getenv(f"{str(provider).upper()}_LIST_USD_PER_1K_CHARS") or "").strip()
    try:
        return max(0.0, float(raw)) if raw else 0.30
    except ValueError:
        return 0.30


def record_informational(
    *,
    provider: str,
    model: str,
    source: str = "",
    kind: str = "voice",
    characters: int = 0,
    plan: str = "subscription",
) -> SpendRecord:
    """Log flat-plan usage. Marginal cost $0; never part of the reel cap."""
    chars = max(0, int(characters or 0))
    list_usd = chars / 1000.0 * subscription_list_rate(provider)
    rec = SpendRecord(
        ts=datetime.now(timezone.utc).isoformat(),
        provider=str(provider or "unknown"),
        model=str(model or "unknown"),
        source=str(source or ""),
        kind=str(kind or "voice"),
        input_tokens=chars,
        output_tokens=0,
        images=0,
        cost_usd=0.0,
        status=f"informational:{plan}:list_usd={list_usd:.4f}",
    )
    with _LOCK:
        _INFO.append(rec)
    _register_exit_dump()
    logger.info(
        "SPEND | provider=%s | model=%s | src=%s | kind=%s | chars=%d | cost=$0 (%s plan, "
        "informational, not in cap)",
        rec.provider, rec.model, rec.source, rec.kind, chars, plan,
    )
    return rec


def reset_reel() -> None:
    with _LOCK:
        _REEL.clear()


def reset_all() -> None:
    """Tests start from an empty ledger."""
    with _LOCK:
        _REEL.clear()
        _RUN.clear()
        _INFO.clear()


def _group(rows: list[SpendRecord]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        key = f"{r.provider}:{r.model}"
        slot = out.setdefault(key, {"calls": 0, "cost_usd": 0.0})
        slot["calls"] += 1
        slot["cost_usd"] = round(slot["cost_usd"] + r.cost_usd, 6)
    return out


def summary() -> dict[str, Any]:
    with _LOCK:
        reel = list(_REEL)
        run = list(_RUN)
        info = list(_INFO)
    return {
        "reel_cap_usd": reel_cap_usd(),
        "reel_llm_usd": round(sum(r.cost_usd for r in reel), 6),
        "reel_stills_usd": round(_stills_spent(), 6),
        "run_llm_usd": round(sum(r.cost_usd for r in run), 6),
        "reel_by_model": _group(reel),
        "run_by_model": _group(run),
        "run_calls": len(run),
        "informational": {
            "note": "flat subscription usage; $0 marginal; not counted toward the reel cap",
            "by_model": _group(info),
            "characters": sum(r.input_tokens for r in info),
        },
    }


def write_ledger(path: str | Path) -> Path:
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        rows = [asdict(r) for r in _RUN]
        info_rows = [asdict(r) for r in _INFO]
    payload = {"summary": summary(), "calls": rows, "informational": info_rows}
    dest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return dest


def _register_exit_dump() -> None:
    """Write ``outputs/logs/spend_<ts>.json`` when the process ends."""
    global _ATEXIT
    if _ATEXIT or os.getenv("PYTEST_CURRENT_TEST"):
        return
    if (os.getenv("SPEND_LEDGER_FILE") or "1").strip().lower() in {"0", "off", "false", "no"}:
        return
    _ATEXIT = True
    import atexit

    def _dump() -> None:
        try:
            from shared.paths import pipeline_logs_dir

            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            dest = write_ledger(pipeline_logs_dir() / f"spend_{stamp}.json")
            logger.info("spend ledger | %s | run_llm=$%.5f", dest, run_total())
        except Exception:  # noqa: BLE001
            pass

    atexit.register(_dump)
