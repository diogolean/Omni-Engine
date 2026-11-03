# -*- coding: utf-8 -*-
"""Resumable YouTube uploads retry HTTP 500-504 a fixed number of times.

The request object is a fake. Nothing in this module opens a socket.
"""

from __future__ import annotations

import copy
import json
import sys
import types
from pathlib import Path

import pytest

from distribution.publishers.api import youtube_upload
from distribution.publishers.api.youtube_upload import (
    YouTubeQuotaExceededError,
    YouTubeResumableUploadError,
    upload_short,
)

_PAGE = "zz_resumable_retry"


class _Resp:
    def __init__(self, status: int, reason: str) -> None:
        self.status = status
        self.reason = reason


class _Progress:
    def __init__(self, sent: int) -> None:
        self.resumable_progress = sent


class _ScriptedRequest:
    """googleapiclient resumable request, with the outcomes queued in memory."""

    def __init__(self, steps: list) -> None:
        self._steps = list(steps)
        self.calls = 0

    def next_chunk(self):
        self.calls += 1
        if not self._steps:
            raise AssertionError(
                f"next_chunk called {self.calls} times, past the scripted outcomes"
            )
        step = self._steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step


class _YouTube:
    def __init__(self, request: _ScriptedRequest) -> None:
        self.request = request
        self.inserts: list[dict] = []

    def channels(self):
        return self

    def list(self, **_kwargs):
        return self

    def execute(self):
        return {
            "items": [
                {
                    "id": "UC_TEST",
                    "snippet": {"title": "Test Channel", "customUrl": ""},
                }
            ]
        }

    def videos(self):
        return self

    def insert(self, **kwargs):
        # upload_short mutates the same body dict on a one-shot retry.
        # Snapshot it here so each insert records the payload it was given.
        self.inserts.append(copy.deepcopy(kwargs))
        return self.request


def _install_google_stub(monkeypatch: pytest.MonkeyPatch) -> type[BaseException]:
    """Use the installed client when it is present. Otherwise a local stand-in."""
    try:
        from googleapiclient.errors import HttpError
    except ImportError:
        HttpError = None  # type: ignore[assignment]
    if HttpError is not None:
        return HttpError

    errors = types.ModuleType("googleapiclient.errors")
    http = types.ModuleType("googleapiclient.http")

    class HttpError(Exception):
        def __init__(self, resp, content, uri=None) -> None:
            self.resp = resp
            self.content = content if isinstance(content, bytes) else str(content).encode()
            self.uri = uri
            super().__init__(self.content.decode("utf-8", "replace"))

    class MediaFileUpload:
        def __init__(self, *args, **kwargs) -> None:
            self.args = args
            self.kwargs = kwargs

    errors.HttpError = HttpError
    http.MediaFileUpload = MediaFileUpload
    package = types.ModuleType("googleapiclient")
    package.errors = errors
    package.http = http
    monkeypatch.setitem(sys.modules, "googleapiclient", package)
    monkeypatch.setitem(sys.modules, "googleapiclient.errors", errors)
    monkeypatch.setitem(sys.modules, "googleapiclient.http", http)
    return HttpError


@pytest.fixture
def http_error(monkeypatch: pytest.MonkeyPatch) -> type[BaseException]:
    return _install_google_stub(monkeypatch)


def _http_error(cls: type[BaseException], status: int, message: str, reason: str = ""):
    body: dict = {"error": {"code": status, "message": message}}
    if reason:
        body["error"]["errors"] = [{"reason": reason, "message": message}]
    return cls(_Resp(status, message), json.dumps(body).encode())


def _video(tmp_path: Path) -> Path:
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"\x00" * 100)
    return path


def _sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    recorded: list[float] = []
    monkeypatch.setattr(youtube_upload.time, "sleep", recorded.append)
    return recorded


def _upload(tmp_path: Path, youtube: _YouTube, **kwargs):
    return upload_short(
        _video(tmp_path),
        title="A quiet clip",
        description="A note",
        tags=["calm"],
        page_name=_PAGE,
        youtube=youtube,
        skip_playlist=True,
        preserve_title=True,
        **kwargs,
    )


def test_resumable_upload_stops_after_fixed_transient_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    http_error: type[BaseException],
) -> None:
    limit = youtube_upload._RESUMABLE_TRANSIENT_RETRIES
    sleeps = _sleeps(monkeypatch)
    request = _ScriptedRequest(
        [_http_error(http_error, 503, "backend error")] * (limit + 3)
    )

    with pytest.raises(YouTubeResumableUploadError, match="retries on HTTP 503") as caught:
        _upload(tmp_path, _YouTube(request))

    err = caught.value
    assert isinstance(err, YouTubeResumableUploadError)
    assert not isinstance(err, YouTubeQuotaExceededError)
    assert f"after {limit} retries" in str(err)
    assert "clip.mp4" in str(err)
    assert _PAGE in str(err)
    assert err.__cause__ is not None
    assert request.calls == limit + 1
    assert sleeps == [youtube_upload._resumable_backoff_seconds(n) for n in range(1, limit + 1)]
    assert request._steps, "the loop consumed every scripted failure instead of stopping"


def test_transient_retries_back_off_then_return_the_video(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    http_error: type[BaseException],
) -> None:
    sleeps = _sleeps(monkeypatch)
    request = _ScriptedRequest(
        [
            _http_error(http_error, 500, "unavailable"),
            _http_error(http_error, 502, "gateway"),
            (_Progress(40), None),
            _http_error(http_error, 503, "unavailable"),
            _http_error(http_error, 504, "timeout"),
            (None, {"id": "abc123"}),
        ]
    )

    youtube = _YouTube(request)
    video_id, url, publish_at = _upload(tmp_path, youtube)

    backoff = youtube_upload._resumable_backoff_seconds
    assert video_id == "abc123"
    assert url == "https://youtu.be/abc123"
    assert publish_at is None
    assert request.calls == 6
    assert len(youtube.inserts) == 1
    # A completed chunk starts the transient count over: 1, 2, then 1, 2 again.
    assert sleeps == [backoff(1), backoff(2), backoff(1), backoff(2)]


def test_quota_error_is_not_retried_even_on_a_transient_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    http_error: type[BaseException],
) -> None:
    sleeps = _sleeps(monkeypatch)
    request = _ScriptedRequest(
        [
            _http_error(http_error, 503, "quotaExceeded", reason="quotaExceeded"),
            _http_error(http_error, 503, "should not be called"),
        ]
    )

    with pytest.raises(YouTubeQuotaExceededError, match="clip.mp4"):
        _upload(tmp_path, _YouTube(request))

    assert request.calls == 1
    assert sleeps == []


def test_non_http_quota_error_is_not_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    http_error: type[BaseException],
) -> None:
    sleeps = _sleeps(monkeypatch)
    request = _ScriptedRequest(
        [RuntimeError("quotaExceeded: upload limit"), RuntimeError("again")]
    )

    with pytest.raises(YouTubeQuotaExceededError, match=_PAGE):
        _upload(tmp_path, _YouTube(request))

    assert request.calls == 1
    assert sleeps == []


@pytest.mark.parametrize("status", [400, 429, 501])
def test_other_http_statuses_are_not_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    http_error: type[BaseException],
    status: int,
) -> None:
    sleeps = _sleeps(monkeypatch)
    request = _ScriptedRequest(
        [
            _http_error(http_error, status, "invalidTags"),
            (None, {"id": "should-not-upload"}),
        ]
    )

    with pytest.raises(http_error):
        _upload(tmp_path, _YouTube(request))

    assert request.calls == 1
    assert sleeps == []


def test_synthetic_media_rejection_still_retries_once_outside_the_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    http_error: type[BaseException],
) -> None:
    """The one-shot field retry stays immediate, and does not spend the 5xx budget."""
    limit = youtube_upload._RESUMABLE_TRANSIENT_RETRIES
    sleeps = _sleeps(monkeypatch)
    request = _ScriptedRequest(
        [
            _http_error(http_error, 400, "containsSyntheticMedia is invalid"),
            *[_http_error(http_error, 503, "backend error") for _ in range(limit)],
            (None, {"id": "after-field"}),
        ]
    )

    youtube = _YouTube(request)
    video_id, _url, _when = _upload(tmp_path, youtube)

    assert video_id == "after-field"
    assert len(youtube.inserts) == 2
    assert youtube.inserts[0]["body"]["status"]["containsSyntheticMedia"] is True
    assert "containsSyntheticMedia" not in youtube.inserts[1]["body"]["status"]
    assert request.calls == limit + 2
    assert sleeps == [
        youtube_upload._resumable_backoff_seconds(n) for n in range(1, limit + 1)
    ]


def test_synthetic_media_rejection_is_still_a_single_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    http_error: type[BaseException],
) -> None:
    sleeps = _sleeps(monkeypatch)
    request = _ScriptedRequest(
        [
            _http_error(http_error, 400, "containsSyntheticMedia is invalid"),
            _http_error(http_error, 400, "containsSyntheticMedia is invalid"),
        ]
    )

    youtube = _YouTube(request)
    with pytest.raises(http_error):
        _upload(tmp_path, youtube)

    assert request.calls == 2
    assert len(youtube.inserts) == 2
    assert "containsSyntheticMedia" not in youtube.inserts[1]["body"]["status"]
    assert sleeps == []


def test_related_video_rejection_is_still_a_single_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    http_error: type[BaseException],
) -> None:
    sleeps = _sleeps(monkeypatch)
    request = _ScriptedRequest(
        [
            _http_error(http_error, 400, "relatedVideoId is invalid"),
            _http_error(http_error, 400, "relatedVideoId is invalid"),
        ]
    )

    youtube = _YouTube(request)
    with pytest.raises(http_error):
        _upload(tmp_path, youtube, related_video_id="long123")

    assert request.calls == 2
    assert len(youtube.inserts) == 2
    assert youtube.inserts[0]["body"]["snippet"]["relatedVideoId"] == "long123"
    assert "relatedVideoId" not in youtube.inserts[1]["body"]["snippet"]
    assert sleeps == []
