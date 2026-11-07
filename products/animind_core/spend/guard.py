"""Fail-closed gate in front of provider network calls."""
from __future__ import annotations

import hashlib
import os
import secrets
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Iterator

from . import contract, ledger, limits as limit_mod, prices
from .prices import TokenRate, UnitRate

_CURRENT: ContextVar["Frame | None"] = ContextVar("animind_spend_frame", default=None)


class SpendDenied(RuntimeError):
    def __init__(self, block: str):
        super().__init__(block)
        self.block = block


@dataclass
class Frame:
    request_id: str
    user_ref: str
    role: str
    job_id: str | None = None
    enforce: bool = True
    block: str | None = None
    limits: limit_mod.Limits | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    seen: dict[tuple, int] = field(default_factory=dict)
    images: int = 0
    origin: str = "live"
    idempotency_key: str | None = None

    def job_ref(self) -> str | None:
        return ref_hash(self.job_id)


def current() -> Frame | None:
    return _CURRENT.get()


def ref_hash(value: str | None) -> str | None:
    if not value:
        return None
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def user_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def new_id() -> str:
    return "spd_" + secrets.token_hex(9)


def request_id() -> str:
    return "req_" + secrets.token_hex(8)


def assess() -> tuple[str | None, limit_mod.Limits | None]:
    block = limit_mod.generation_block()
    if block:
        return block, None
    if prices.operator_table_invalid():
        return "unavailable", None
    found = limit_mod.read_limits()
    if found is None:
        return "unavailable", None
    if not ledger.configured() or not ledger.ping():
        return "ledger", found
    return None, found


def open_frame(*, user_ref: str, role: str, request: str, origin: str = "live") -> Frame:
    block, found = assess()
    return Frame(
        request_id=request, user_ref=user_ref, role=role, block=block, limits=found, origin=origin,
    )


@contextmanager
def frame_scope(frame: Frame) -> Iterator[Frame]:
    token = _CURRENT.set(frame)
    try:
        yield frame
    finally:
        _CURRENT.reset(token)


@contextmanager
def cli_scope() -> Iterator[Frame]:
    frame = open_frame(user_ref=user_hash("cli"), role="user", request=request_id(), origin="live")
    with frame_scope(frame):
        yield frame


class Attempt:
    def __init__(self, *, enforced: bool, timeout_s: float | None, entry_id: str | None, price: Any, hold_cents: int | None):
        self.enforced = enforced
        self.entry_id = entry_id
        self.price = price
        self.hold_cents = hold_cents
        self.sent = False
        self.observation: dict[str, Any] = {}
        self.started = time.perf_counter()
        self._timeout_s = timeout_s

    def mark_sent(self) -> None:
        self.sent = True

    def timeout(self, default: float) -> float:
        if not self.enforced or self._timeout_s is None:
            return default
        return min(default, self._timeout_s)

    def observe(self, **kwargs: Any) -> None:
        self.observation = kwargs


class _NullAttempt(Attempt):
    def __init__(self) -> None:
        super().__init__(enforced=False, timeout_s=None, entry_id=None, price=None, hold_cents=None)

    def mark_sent(self) -> None:
        return None

    def observe(self, **kwargs: Any) -> None:
        return None


def _prompt_hash(prompt: str | None) -> str | None:
    if not prompt:
        return None
    return hashlib.sha256(prompt.encode()).hexdigest()[:16]


def _ceiling(price: Any, found: limit_mod.Limits, images: int) -> int | None:
    if isinstance(price, TokenRate):
        micro = price.ceiling_micro(found.max_input_tokens, found.max_output_tokens)
    elif isinstance(price, UnitRate) and price.unit == "image":
        micro = price.micro_usd * max(1, images)
    elif isinstance(price, UnitRate) and price.unit == "audio_second":
        micro = price.micro_usd * found.max_audio_seconds
    elif isinstance(price, UnitRate) and price.unit == "video_second":
        micro = price.micro_usd * found.max_video_seconds
    elif isinstance(price, UnitRate):
        micro = price.micro_usd
    else:
        return None
    if micro <= 0:
        return None
    return prices.micro_to_cents(micro)


def _actual_micro(price: Any, obs: dict[str, Any]) -> int | None:
    if isinstance(price, TokenRate):
        if obs.get("input_tokens") is None and obs.get("output_tokens") is None:
            return None
        return prices.estimate_micro(
            price, int(obs.get("input_tokens") or 0), int(obs.get("output_tokens") or 0), obs.get("cached_tokens"),
        )
    if isinstance(price, UnitRate) and price.unit == "image":
        count = obs.get("images")
        if count is None:
            return None
        return price.micro_usd * int(count)
    if isinstance(price, UnitRate):
        return price.micro_usd
    return None


def _reported_micro(obs: dict[str, Any]) -> int | None:
    raw = obs.get("reported_usd")
    if raw is None:
        return None
    try:
        micro = int(round(float(raw) * 1_000_000))
    except (TypeError, ValueError):
        return None
    return micro


@contextmanager
def billable(
    *,
    provider: str,
    model: str | None,
    operation: str,
    prompt: str | None = None,
    images: int = 0,
    stream: bool = False,
) -> Iterator[Attempt]:
    frame = current()
    if frame is None:
        # Adapter unit tests do not set ANIMIND_GENERATION. A process that did
        # set it cannot reach a provider by skipping the request frame.
        if os.getenv("ANIMIND_GENERATION") is None:
            yield _NullAttempt()
            return
        origin = "test" if os.getenv("ANIMIND_SPEND_ORIGIN") == "test" else "live"
        frame = open_frame(user_ref=user_hash("library"), role="user", request=request_id(), origin=origin)
    elif not frame.enforce:
        yield _NullAttempt()
        return
    price = prices.lookup(provider, model or "")
    attempt_n = _next_attempt(frame, provider, model or "", operation, prompt)
    if frame.block:
        _remember(frame, provider, model, operation, attempt_n, "blocked", frame.block, price, None, {})
        raise SpendDenied(frame.block)
    if stream:
        _remember(frame, provider, model, operation, attempt_n, "blocked", "stream", price, None, {})
        raise SpendDenied("stream")
    found = frame.limits
    if found is None or price is None:
        block = "price_unavailable" if found is not None and price is None else (frame.block or "unavailable")
        _remember(frame, provider, model, operation, attempt_n, "blocked", block, price, None, {})
        raise SpendDenied(block)
    if images and frame.images + images > found.max_images:
        _remember(frame, provider, model, operation, attempt_n, "blocked", "over_cap", price, None, {})
        raise SpendDenied("over_cap")
    if attempt_n > found.max_retries + 1:
        _remember(frame, provider, model, operation, attempt_n, "blocked", "over_cap", price, None, {})
        raise SpendDenied("over_cap")
    cents = _ceiling(price, found, images or 1)
    if cents is None:
        _remember(frame, provider, model, operation, attempt_n, "blocked", "price_unavailable", price, None, {})
        raise SpendDenied("price_unavailable")
    entry_id = new_id()
    entry = _entry(frame, entry_id, provider, model, operation, attempt_n, "reserved", None, price, cents, {})
    try:
        result = ledger.reserve(entry, cents, found)
    except ledger.LedgerUnavailable:
        _remember(frame, provider, model, operation, attempt_n, "blocked", "ledger", price, None, {})
        raise SpendDenied("ledger") from None
    if result != "ok":
        _remember(frame, provider, model, operation, attempt_n, "blocked", result, price, None, {})
        raise SpendDenied(result)
    if images:
        frame.images += images
    attempt = Attempt(
        enforced=True,
        timeout_s=found.timeout_ms / 1000,
        entry_id=entry_id,
        price=price,
        hold_cents=cents,
    )
    try:
        yield attempt
    except SpendDenied:
        raise
    except Exception:
        status = "uncertain" if attempt.sent else "failed"
        _finish(frame, attempt, provider, model, operation, attempt_n, status, status if status == "uncertain" else None)
        raise
    else:
        _finish(frame, attempt, provider, model, operation, attempt_n, "succeeded", None)


def _next_attempt(frame: Frame, provider: str, model: str, operation: str, prompt: str | None) -> int:
    digest = _prompt_hash(prompt)
    if digest is None:
        return 1
    key = (provider, model, operation, digest)
    seen = frame.seen.get(key, 0) + 1
    frame.seen[key] = seen
    return seen


def _entry(
    frame: Frame, entry_id: str, provider: str, model: str | None, operation: str, attempt: int,
    status: str, block: str | None, price: Any, hold_cents: int | None, obs: dict[str, Any],
) -> dict[str, Any]:
    estimated = _actual_micro(price, obs) if price is not None and status == "succeeded" else None
    reported = _reported_micro(obs) if status == "succeeded" else None
    return {
        "id": entry_id,
        "request_id": frame.request_id,
        "attempt": attempt,
        "job_ref": frame.job_ref(),
        "user_id": frame.user_ref,
        "operation": operation,
        "provider": provider,
        "model": model,
        "status": status,
        "block": block,
        "reason": None,
        "latency_ms": obs.get("latency_ms"),
        "input_tokens": obs.get("input_tokens"),
        "output_tokens": obs.get("output_tokens"),
        "cached_tokens": obs.get("cached_tokens"),
        "thinking_tokens": obs.get("thinking_tokens"),
        "image_count": obs.get("images"),
        "audio_seconds": obs.get("audio_seconds"),
        "video_seconds": obs.get("video_seconds"),
        "estimated_micro": estimated,
        "reported_micro": reported,
        "reserved_cents": hold_cents,
        "currency": "USD" if hold_cents else None,
        "price_version": getattr(price, "version", None) if price is not None else None,
        "origin": frame.origin,
        "idempotency_key": frame.idempotency_key,
    }


def _event(frame: Frame, entry: dict[str, Any]) -> dict[str, Any]:
    return contract.call_record(
        entry_id=entry["id"], request_id=entry["request_id"], attempt=entry["attempt"],
        job_ref=entry.get("job_ref"), user_ref=entry["user_id"], operation=entry["operation"],
        provider=entry["provider"], model=entry.get("model"), status=entry["status"], block=entry.get("block"),
        latency_ms=entry.get("latency_ms"), input_tokens=entry.get("input_tokens"),
        output_tokens=entry.get("output_tokens"), cached_tokens=entry.get("cached_tokens"),
        thinking_tokens=entry.get("thinking_tokens"), images=entry.get("image_count"),
        audio_seconds=entry.get("audio_seconds"), video_seconds=entry.get("video_seconds"),
        estimated_micro=entry.get("estimated_micro"), reported_micro=entry.get("reported_micro"),
        price_version=entry.get("price_version"), hold_cents=entry.get("reserved_cents"),
        reason=entry.get("reason"),
    )


def _remember(
    frame: Frame, provider: str, model: str | None, operation: str, attempt: int, status: str,
    block: str | None, price: Any, hold: int | None, obs: dict[str, Any],
) -> None:
    entry = _entry(frame, new_id(), provider, model, operation, attempt, status, block, price, hold, obs)
    entry["reason"] = block
    frame.events.append(_event(frame, entry))
    try:
        ledger.insert_status(entry)
    except ledger.LedgerUnavailable:
        return None


def _finish(
    frame: Frame, attempt: Attempt, provider: str, model: str | None, operation: str, attempt_n: int,
    status: str, block: str | None,
) -> None:
    obs = dict(attempt.observation)
    obs["latency_ms"] = int((time.perf_counter() - attempt.started) * 1000)
    estimated = _actual_micro(attempt.price, obs) if status == "succeeded" else None
    reported = _reported_micro(obs) if status == "succeeded" else None
    entry = _entry(
        frame, attempt.entry_id or new_id(), provider, model, operation, attempt_n, status, block,
        attempt.price, attempt.hold_cents, obs,
    )
    entry["estimated_micro"] = estimated
    entry["reported_micro"] = reported
    frame.events.append(_event(frame, entry))
    if attempt.entry_id:
        try:
            ledger.settle(attempt.entry_id, status, entry)
        except ledger.LedgerUnavailable:
            return None


def note_unbilled(
    *,
    provider: str,
    model: str | None,
    operation: str,
    reason: str,
    attempt: int = 1,
    audio_seconds: float | None = None,
    images: int | None = None,
) -> None:
    """A local cache hit or an unpriced transport. Cost stays null, not zero."""
    frame = current()
    if frame is None:
        return
    entry = _entry(frame, new_id(), provider, model, operation, attempt, "not_charged", None, None, None, {
        "audio_seconds": audio_seconds, "images": images,
    })
    entry["reason"] = reason
    entry["estimated_micro"] = None
    entry["reported_micro"] = None
    entry["currency"] = None
    frame.events.append(_event(frame, entry))
    try:
        ledger.insert_status(entry)
    except ledger.LedgerUnavailable:
        return None
