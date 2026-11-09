"""HTTP helpers. Polling and file reads do not reserve."""
from __future__ import annotations

import json
import re
from typing import Any, Callable

from fastapi import HTTPException, Request

from . import contract, ledger
from .guard import SpendDenied, frame_scope, open_frame, request_id, user_hash
from .limits import read_limits, request_over_cap

_USER = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_KEY = re.compile(r"^[A-Za-z0-9_-]{8,80}$")
_REQUEST = re.compile(r"^[A-Za-z0-9_-]{8,80}$")

_STATUS = {
    "disabled": 403,
    "approval": 403,
    "credential": 503,
    "user": 400,
    "conflict": 409,
    "budget": 402,
    "request_budget": 402,
    "rate": 429,
    "busy": 429,
    "over_cap": 400,
    "price_unavailable": 503,
    "unavailable": 503,
    "ledger": 503,
    "timeout": 504,
    "stream": 503,
    "replay": 409,
}


def http_error(block: str) -> HTTPException:
    return HTTPException(_STATUS.get(block, 503), f"spend:{block}")


def parse_user(request: Request) -> tuple[str, str]:
    raw = (request.headers.get("x-animind-user") or "").strip()
    role = "admin" if (request.headers.get("x-animind-role") or "").strip() == "admin" else "user"
    if not raw:
        return user_hash("anonymous"), role
    if "@" in raw or not _USER.match(raw):
        raise http_error("user")
    return user_hash(raw), role


def parse_key(request: Request) -> str | None:
    raw = request.headers.get("idempotency-key")
    if raw is None or raw.strip() == "":
        return None
    key = raw.strip()
    if not _KEY.match(key):
        raise HTTPException(400, "spend:idempotency")
    return key


def parse_request_id(request: Request) -> str:
    raw = (request.headers.get("x-request-id") or "").strip()
    if _REQUEST.match(raw):
        return raw
    return request_id()


def require_explicit_identity(request: Request) -> None:
    """Paid avatar execution must be replayable across process boundaries."""
    if parse_key(request) is None:
        raise HTTPException(400, "spend:idempotency_required")
    raw = (request.headers.get("x-request-id") or "").strip()
    if not _REQUEST.match(raw):
        raise HTTPException(400, "spend:request_id_required")


def body_hash(body: dict[str, Any]) -> str:
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return user_hash(encoded)


def _client_origin(request: Request) -> str:
    """Hash of the caller address. The raw address is not stored."""
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    host = request.client.host if request.client is not None else ""
    return user_hash(forwarded or host or "unknown")


def protect(request: Request, body: dict[str, Any], operation: str, handler: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    user_ref, role = parse_user(request)
    key = parse_key(request)
    frame = open_frame(
        user_ref=user_ref, role=role, request=parse_request_id(request), origin=_origin(),
        origin_ref=_client_origin(request),
    )
    frame.idempotency_key = key
    if _trusted_admin(request) and frame.block == "disabled":
        _lift_disabled_for_admin(frame)
    digest = body_hash(body if isinstance(body, dict) else {"_": str(type(body))})
    with frame_scope(frame):
        if operation in {"render", "character", "avatar"} and frame.block == "ledger":
            raise http_error("ledger")
        replay = None
        if key:
            replay = _replay(frame, user_ref, key, digest, operation, body)
        if replay is not None:
            replay.setdefault("usage", contract.document(frame))
            return replay
        if frame.limits is not None and isinstance(body, dict):
            over = request_over_cap(body, frame.limits)
            if over:
                raise http_error("over_cap")
        try:
            result = handler()
        except SpendDenied as exc:
            raise http_error(exc.block) from exc
        if not isinstance(result, dict):
            result = {"result": result}
        result = dict(result)
        result["usage"] = contract.document(frame)
        if key:
            _save(user_ref, key, digest, result.get("id"))
        return result


def _trusted_admin(request: Request) -> bool:
    from products.animind_core.admin_auth import trusted  # noqa: PLC0415

    return trusted(request.headers.get("x-animind-admin"))


def _lift_disabled_for_admin(frame) -> None:
    """An admin may pass the global off switch. The ledger and the cap stay."""
    limits = read_limits()
    if limits is None:
        frame.block = "unavailable"
        return
    if not ledger.configured() or not ledger.ping():
        frame.block = "ledger"
        frame.limits = limits
        return
    frame.block = None
    frame.limits = limits


def _origin() -> str:
    import os
    return "test" if (os.getenv("ANIMIND_SPEND_ORIGIN") or "").strip() == "test" else "live"


def _replay(frame, user_ref: str, key: str, digest: str, operation: str, body: dict[str, Any]) -> dict[str, Any] | None:
    if frame.block == "ledger" or not ledger.configured():
        raise http_error("ledger")
    try:
        found = ledger.find_replay(user_ref, key)
    except ledger.LedgerUnavailable:
        raise http_error("ledger") from None
    if found is None:
        return None
    if found["body_hash"] != digest:
        raise http_error("conflict")
    if operation == "plan":
        from .plan import build
        return build(body)
    if operation == "render":
        from products.animind_core import render
        job = render.get(str(found.get("resource_id") or ""))
        if job is not None:
            return job
        return {"id": found.get("resource_id"), "status": "unknown", "progress": 0.0, "replay": True}
    if operation == "character":
        from products.animind_core import characters
        saved = characters.load(str(found.get("resource_id") or ""))
        if saved is not None:
            return saved
        return {"id": found.get("resource_id"), "replay": True}
    if operation == "avatar":
        from products.animind_core import admin_avatar, avatar_request

        avatar_id = str(found.get("resource_id") or "")
        saved = admin_avatar.get(user_ref, avatar_id) or avatar_request.get(avatar_id, user_ref)
        if saved is not None:
            return saved
        return {"id": avatar_id, "status": "unknown", "replay": True}
    return {"id": found.get("resource_id"), "replay": True}


def _save(user_ref: str, key: str, digest: str, resource_id: Any) -> None:
    try:
        ledger.save_replay(user_ref, key, digest, None if resource_id is None else str(resource_id), 200)
    except ledger.LedgerUnavailable:
        raise http_error("ledger") from None
