"""Public usage document. Null cost is unknown. It is not zero."""
from __future__ import annotations

from typing import Any

SCHEMA = "animind.usage.v1"


def call_record(
    *,
    entry_id: str,
    request_id: str,
    attempt: int,
    job_ref: str | None,
    user_ref: str,
    operation: str,
    provider: str,
    model: str | None,
    status: str,
    block: str | None,
    latency_ms: int | None,
    input_tokens: int | None,
    output_tokens: int | None,
    cached_tokens: int | None,
    thinking_tokens: int | None,
    images: int | None,
    audio_seconds: float | None,
    video_seconds: float | None,
    estimated_micro: int | None,
    reported_micro: int | None,
    price_version: str | None,
    hold_cents: int | None,
    reason: str | None = None,
) -> dict[str, Any]:
    if reported_micro is not None:
        cost_status = "reported"
    elif estimated_micro is not None:
        cost_status = "estimated"
    elif status == "not_charged":
        cost_status = "not_charged"
    else:
        cost_status = "unknown"
    currency = "USD" if cost_status in {"reported", "estimated"} or hold_cents else None
    return {
        "id": entry_id,
        "requestId": request_id,
        "attempt": attempt,
        "jobRef": job_ref,
        "userRef": user_ref,
        "operation": operation,
        "provider": provider,
        "model": model,
        "status": status,
        "block": block,
        "reason": reason,
        "latencyMs": latency_ms,
        "usage": {
            "inputTokens": input_tokens,
            "outputTokens": output_tokens,
            "cachedTokens": cached_tokens,
            "thinkingTokens": thinking_tokens,
        },
        "units": {
            "images": images,
            "audioSeconds": audio_seconds,
            "videoSeconds": video_seconds,
        },
        "cost": {
            "currency": currency,
            "estimatedMicro": estimated_micro,
            "reportedMicro": reported_micro,
            "status": cost_status,
            "priceVersion": price_version,
            "holdCents": hold_cents,
        },
    }


def totals(calls: list[dict[str, Any]]) -> dict[str, Any]:
    billed = [row for row in calls if row.get("status") in {"succeeded", "uncertain"}]
    estimated = [row["cost"]["estimatedMicro"] for row in billed if row.get("cost", {}).get("estimatedMicro") is not None]
    reported = [row["cost"]["reportedMicro"] for row in billed if row.get("cost", {}).get("reportedMicro") is not None]
    holds = [row["cost"]["holdCents"] for row in calls if row.get("cost", {}).get("holdCents")]
    return {
        "calls": len(calls),
        "blocked": sum(1 for row in calls if row.get("status") == "blocked"),
        "estimatedMicro": sum(estimated) if estimated else None,
        "reportedMicro": sum(reported) if reported else None,
        "estimatedUnknown": sum(1 for row in billed if row.get("cost", {}).get("estimatedMicro") is None),
        "reportedUnknown": sum(1 for row in billed if row.get("cost", {}).get("reportedMicro") is None),
        "holdCents": sum(holds) if holds else 0,
    }


def document(frame: Any, job_id: str | None = None) -> dict[str, Any]:
    calls = list(frame.events)
    return {
        "schema": SCHEMA,
        "requestId": frame.request_id,
        "jobId": job_id or frame.job_id,
        "userRef": frame.user_ref,
        "calls": calls,
        "totals": totals(calls),
    }


def empty(request_id: str, user_ref: str, job_id: str | None = None) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "requestId": request_id,
        "jobId": job_id,
        "userRef": user_ref,
        "calls": [],
        "totals": totals([]),
    }


def from_rows(rows: list[dict[str, Any]], *, request_id: str, user_ref: str, job_id: str | None) -> dict[str, Any]:
    calls = [
        call_record(
            entry_id=row["id"],
            request_id=row.get("request_id") or request_id,
            attempt=int(row.get("attempt") or 1),
            job_ref=row.get("job_ref"),
            user_ref=row.get("user_id") or user_ref,
            operation=row.get("operation") or "call",
            provider=row.get("provider") or "unknown",
            model=row.get("model"),
            status=row.get("status") or "uncertain",
            block=row.get("block"),
            latency_ms=row.get("latency_ms"),
            input_tokens=row.get("input_tokens"),
            output_tokens=row.get("output_tokens"),
            cached_tokens=row.get("cached_tokens"),
            thinking_tokens=row.get("thinking_tokens"),
            images=row.get("image_count"),
            audio_seconds=row.get("audio_seconds"),
            video_seconds=row.get("video_seconds"),
            estimated_micro=row.get("estimated_micro"),
            reported_micro=row.get("reported_micro"),
            price_version=row.get("price_version"),
            hold_cents=row.get("reserved_cents"),
            reason=row.get("reason"),
        )
        for row in rows
    ]
    return {"schema": SCHEMA, "requestId": request_id, "jobId": job_id, "userRef": user_ref, "calls": calls, "totals": totals(calls)}
