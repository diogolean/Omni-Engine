# -*- coding: utf-8 -*-
"""Publisher interface: retries, idempotency, and mocked HTTP."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import requests

from accounts.brandings import load_brandings
from distribution.publishers.api.pinterest import PinterestAccountPublisher
from distribution.publishers.api.youtube import YouTubeAccountPublisher
from distribution.publishers.base import (
    Account,
    Content,
    FileIdempotencyStore,
    MemoryIdempotencyStore,
    PostJsonPublisher,
)
from distribution.publishers.registry import publisher_for


class _Response:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text or json.dumps(self._payload)
        self.content = self.text.encode()

    def json(self) -> dict:
        return self._payload


class _Session:
    def __init__(self, responses: list[_Response]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def post(self, url, json=None, headers=None, timeout=None):  # noqa: A002
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        if not self._responses:
            raise AssertionError("unexpected HTTP call")
        return self._responses.pop(0)


def _account(account_id: str = "ancient_knowledge", *, active: bool = True) -> Account:
    return Account(
        branding=account_id,
        platform="http",
        account_id=account_id,
        active=active,
        token_env=f"TOKEN_{account_id.upper()}",
    )


def _content(key: str = "pin-1") -> Content:
    return Content(idempotency_key=key, title="Stone circle", body="A quiet note.")


def test_retries_transient_http_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOKEN_ANCIENT_KNOWLEDGE", "tok-ak")
    session = _Session([_Response(503), _Response(201, {"id": "pin_42"})])
    delays: list[float] = []
    publisher = PostJsonPublisher(
        "https://api.example.test/pins",
        session=session,
        store=MemoryIdempotencyStore(),
        max_retries=3,
        backoff_base_sec=10,
        sleeper=delays.append,
    )
    result = publisher.publish(_content(), _account())
    assert result.ok is True
    assert result.external_id == "pin_42"
    assert result.duplicate is False
    assert len(session.calls) == 2
    assert delays == [10]
    assert session.calls[0]["headers"]["Authorization"] == "Bearer tok-ak"
    assert session.calls[0]["json"]["idempotency_key"] == "pin-1"


def test_client_error_is_not_retried() -> None:
    session = _Session([_Response(400, text="bad title")])
    publisher = PostJsonPublisher(
        "https://api.example.test/pins",
        session=session,
        store=MemoryIdempotencyStore(),
        sleeper=lambda _seconds: (_ for _ in ()).throw(AssertionError("should not sleep")),
    )
    result = publisher.publish(_content(), _account())
    assert result.ok is False
    assert result.error == "HTTP 400"
    assert len(session.calls) == 1


def test_idempotency_key_skips_the_second_http_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOKEN_ANCIENT_KNOWLEDGE", "tok-ak")
    session = _Session([_Response(201, {"id": "pin_7"})])
    publisher = PostJsonPublisher(
        "https://api.example.test/pins",
        session=session,
        store=MemoryIdempotencyStore(),
    )
    content = _content("same-key")
    account = _account()
    first = publisher.publish(content, account)
    second = publisher.publish(content, account)
    assert first.external_id == "pin_7"
    assert second.duplicate is True
    assert second.skipped is True
    assert second.external_id == "pin_7"
    assert len(session.calls) == 1


def test_inactive_account_does_not_call_http() -> None:
    session = _Session([])
    publisher = PostJsonPublisher(
        "https://api.example.test/pins",
        session=session,
        store=MemoryIdempotencyStore(),
    )
    result = publisher.publish(_content(), _account(active=False))
    assert result.ok is True
    assert result.skipped is True
    assert result.error == "inactive"
    assert session.calls == []


def test_ledger_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOKEN_ANCIENT_KNOWLEDGE", "tok-ak")
    ledger = tmp_path / "publish_ledger.json"
    session = _Session([_Response(201, {"id": "pin_9"})])
    publisher = PostJsonPublisher(
        "https://api.example.test/pins",
        session=session,
        store=FileIdempotencyStore(ledger),
    )
    publisher.publish(_content("ledger-key"), _account())
    reloaded = PostJsonPublisher(
        "https://api.example.test/pins",
        session=_Session([]),
        store=FileIdempotencyStore(ledger),
    )
    again = reloaded.publish(_content("ledger-key"), _account())
    assert again.duplicate is True
    assert again.external_id == "pin_9"


def test_pinterest_adapter_posts_with_mocked_http(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OUTPUT_PATH", str(tmp_path / "outputs"))
    monkeypatch.setenv("OMNI_LEDGER_PATH", str(tmp_path / "ledger.json"))
    monkeypatch.setenv("PINTEREST_ACCESS_TOKEN_ANCIENT_KNOWLEDGE", "tok-ancient")
    monkeypatch.setenv("PINTEREST_BOARD_ID_ANCIENT_KNOWLEDGE", "board-ancient")
    monkeypatch.setenv("PINTEREST_ACCESS_TOKEN_ANNA_PROTOCOL", "tok-anna")
    monkeypatch.setenv("PINTEREST_BOARD_ID_ANNA_PROTOCOL", "board-anna")

    captured: list[dict] = []

    def fake_post(url, headers=None, data=None, timeout=None, **_kwargs):
        body = json.loads(data)
        captured.append(
            {
                "url": url,
                "auth": (headers or {}).get("Authorization"),
                "board_id": body.get("board_id"),
                "title": body.get("title"),
            }
        )
        response = _Response(201, {"id": f"pin_{len(captured)}"})
        return response

    import distribution.publishers.api.pinterest as pin_mod

    monkeypatch.setattr(pin_mod.requests, "post", fake_post)

    store = MemoryIdempotencyStore()
    publisher = PinterestAccountPublisher(store=store, max_retries=1, sleeper=lambda _s: None)
    ancient = next(
        account
        for account in (spec.to_account() for spec in _pinterest_specs())
        if account.account_id == "ancient_knowledge"
    )
    anna = next(
        account
        for account in (spec.to_account() for spec in _pinterest_specs())
        if account.account_id == "anna_protocol"
    )
    image = b"\xff\xd8fake-jpeg"
    first = publisher.publish(
        Content(
            idempotency_key="ancient-1",
            title="Göbekli",
            body="A quiet note about stone circles.",
            media_bytes=image,
        ),
        ancient,
    )
    second = publisher.publish(
        Content(
            idempotency_key="anna-1",
            title="Protocol",
            body="A quiet note about the protocol.",
            media_bytes=image,
        ),
        anna,
    )
    assert first.ok and first.external_id == "pin_1"
    assert second.ok and second.external_id == "pin_2"
    assert captured[0]["url"] == "https://api.pinterest.com/v5/pins"
    assert captured[0]["auth"] == "Bearer tok-ancient"
    assert captured[0]["board_id"] == "board-ancient"
    assert captured[1]["auth"] == "Bearer tok-anna"
    assert captured[1]["board_id"] == "board-anna"

    replay = publisher.publish(
        Content(idempotency_key="ancient-1", title="Göbekli", media_bytes=image),
        ancient,
    )
    assert replay.duplicate is True
    assert len(captured) == 2


def test_inactive_pinterest_account_skips_before_http() -> None:
    wonder = next(row for row in load_brandings() if row.branding_id == "wonder_feed")
    spec = wonder.platform("pinterest")
    assert spec is not None
    publisher = PinterestAccountPublisher(store=MemoryIdempotencyStore())
    result = publisher.publish(
        Content(idempotency_key="wonder-1", title="skip", media_bytes=b"x"),
        spec.to_account(),
    )
    assert result.skipped is True
    assert result.error == "inactive"


def test_youtube_adapter_delegates_and_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    def fake_upload(**kwargs):
        calls.append(kwargs)
        return ("abc123", "https://youtu.be/abc123", None)

    monkeypatch.setattr(
        "distribution.publishers.api.youtube_upload.upload_short_from_envelope",
        fake_upload,
    )

    account = Account(
        branding="ancient_knowledge",
        platform="youtube",
        account_id="ancient_knowledge",
        active=True,
        token_file="credentials/tokens/youtube_token_ancient_knowledge.json",
    )
    publisher = YouTubeAccountPublisher(store=MemoryIdempotencyStore(), max_retries=1)
    content = Content(
        idempotency_key="yt-1",
        title="Short title",
        body="Caption",
        media_path="/tmp/clip.mp4",
    )
    result = publisher.publish(content, account)
    again = publisher.publish(content, account)
    assert result.ok is True
    assert result.external_id == "abc123"
    assert again.duplicate is True
    assert len(calls) == 1
    assert calls[0]["page_name"] == "ancient_knowledge"
    assert calls[0]["row"]["video_path"] == "/tmp/clip.mp4"
    assert calls[0]["row"]["title"] == "Short title"


def test_facebook_has_no_api_publisher() -> None:
    with pytest.raises(NotImplementedError):
        publisher_for("facebook")


def test_requests_mock_is_the_http_library(monkeypatch: pytest.MonkeyPatch) -> None:
    """The retry helper goes through requests, which tests replace."""
    monkeypatch.setenv("TOKEN_ANCIENT_KNOWLEDGE", "tok-ak")
    response = _Response(201, {"id": "from-requests"})

    def fake_post(self, url, json=None, headers=None, timeout=None):  # noqa: A002
        assert url == "https://api.example.test/pins"
        assert headers["Authorization"] == "Bearer tok-ak"
        return response

    monkeypatch.setattr(requests.Session, "post", fake_post)
    publisher = PostJsonPublisher(
        "https://api.example.test/pins",
        store=MemoryIdempotencyStore(),
    )
    result = publisher.publish(_content("req-1"), _account())
    assert result.external_id == "from-requests"


def _pinterest_specs():
    for branding in load_brandings():
        spec = branding.platform("pinterest")
        if spec is not None and spec.active:
            yield spec
