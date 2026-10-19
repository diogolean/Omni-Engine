# -*- coding: utf-8 -*-
"""Daily B2 budgets, listing cache, and single-file downloads."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from agents.media.b2_budget import (
    B2DailyCapError,
    choose_publish_key,
    reset_caches,
)
from agents.media.b2_client import (
    download_object,
    list_object_keys,
    read_object_text,
    upload_object,
)


class _CapError(Exception):
    def __init__(self) -> None:
        super().__init__("download cap exceeded")
        self.response = {
            "Error": {"Code": "403", "Message": "download bandwidth cap exceeded"},
            "ResponseMetadata": {"HTTPStatusCode": 403},
        }


class _Body:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


class _Client:
    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self.objects = objects or {}
        self.lists = 0
        self.gets = 0
        self.downloads: list[str] = []

    def list_objects_v2(self, **kwargs):
        self.lists += 1
        prefix = kwargs.get("Prefix") or ""
        contents = [
            {"Key": key, "Size": len(body)}
            for key, body in self.objects.items()
            if key.startswith(prefix)
        ]
        return {"Contents": contents, "IsTruncated": False}

    def get_object(self, **kwargs):
        self.gets += 1
        key = kwargs["Key"]
        if key not in self.objects:
            raise FileNotFoundError(key)
        return {"Body": _Body(self.objects[key])}

    def download_file(self, _bucket, key, filename):
        self.downloads.append(key)
        Path(filename).write_bytes(self.objects[key])

    def put_object(self, **kwargs):
        body = kwargs.get("Body") or b""
        self.objects[kwargs["Key"]] = bytes(body)
        return {}


@pytest.fixture
def budget(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("B2_USAGE_PATH", str(tmp_path / "usage.json"))
    monkeypatch.delenv("B2_DAILY_DOWNLOAD_MB", raising=False)
    monkeypatch.delenv("B2_DAILY_CLASS_B", raising=False)
    monkeypatch.delenv("B2_DAILY_CLASS_C", raising=False)
    monkeypatch.delenv("B2_LIST_CACHE_TTL_SECONDS", raising=False)
    reset_caches()
    return tmp_path


def test_counters_reset_at_utc_midnight(budget: Path) -> None:
    path = budget / "usage.json"
    path.write_text(
        json.dumps(
            {
                "day": "2000-01-01",
                "download_bytes": 999,
                "class_b": 9,
                "class_c": 1499,
                "logged": {},
            }
        ),
        encoding="utf-8",
    )
    client = _Client({"notes/a.txt": b"hi"})
    keys = list_object_keys("notes/", client=client, bucket="bucket")
    assert keys == ["notes/a.txt"]
    state = json.loads(path.read_text(encoding="utf-8"))
    assert state["day"] != "2000-01-01"
    assert state["class_c"] == 1
    assert state["download_bytes"] == 0
    assert state["class_b"] == 0


def test_listing_cache_skips_a_second_class_c_call(budget: Path) -> None:
    del budget
    client = _Client({"lib/post_a.json": b"{}"})
    first = list_object_keys("lib/", client=client, bucket="bucket")
    second = list_object_keys("lib/", client=client, bucket="bucket")
    assert first == second == ["lib/post_a.json"]
    assert client.lists == 1


def test_ledger_read_is_cached_until_the_object_is_replaced(budget: Path) -> None:
    del budget
    key = "pinterest/ledger/ancient.jsonl"
    client = _Client({key: b"one\n"})
    assert read_object_text(key, client=client, bucket="bucket") == "one\n"
    assert read_object_text(key, client=client, bucket="bucket") == "one\n"
    assert client.gets == 1
    upload_object(
        key,
        b"one\ntwo\n",
        client=client,
        bucket="bucket",
        content_type="application/x-ndjson",
    )
    text = read_object_text(key, client=client, bucket="bucket")
    assert text == "one\ntwo\n"
    assert client.gets == 1


def test_download_refuses_a_known_oversized_object_and_prefers_the_image(
    budget: Path, tmp_path: Path
) -> None:
    del budget
    client = _Client(
        {
            "clips/story.mp4": b"v" * 50,
            "clips/story-big.jpg": b"b" * 40,
            "clips/story-small.jpg": b"s" * 8,
        }
    )
    list_object_keys("clips/", client=client, bucket="bucket")
    chosen = choose_publish_key(
        list(client.objects),
        {
            "clips/story.mp4": 10_000_000_000,
            "clips/story-big.jpg": 4000,
            "clips/story-small.jpg": 800,
        },
    )
    assert chosen == "clips/story-small.jpg"
    with pytest.raises(B2DailyCapError):
        download_object("clips/story.mp4", tmp_path / "story.mp4", client=client, bucket="bucket")
    assert client.downloads == []
    download_object(chosen, tmp_path / "story.jpg", client=client, bucket="bucket")
    assert client.downloads == [chosen]


def test_known_size_over_the_download_budget_is_not_fetched(
    budget: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    del budget
    monkeypatch.setenv("B2_DAILY_DOWNLOAD_MB", "1")
    client = _Client({"clips/huge.jpg": b"x"})
    from agents.media.b2_budget import client_token, store_listing

    store_listing(
        client_token(client),
        "bucket",
        "clips/",
        ["clips/huge.jpg"],
        {"clips/huge.jpg": 20 * 1024 * 1024},
    )
    with pytest.raises(B2DailyCapError):
        download_object("clips/huge.jpg", tmp_path / "huge.jpg", client=client, bucket="bucket")
    assert client.downloads == []


def test_cap_403_backs_off_then_stops(
    budget: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del budget
    delays: list[float] = []
    monkeypatch.setattr("agents.media.b2_budget._sleep", lambda seconds: delays.append(seconds))

    class _Down(_Client):
        def get_object(self, **kwargs):
            self.gets += 1
            raise _CapError()

    client = _Down()
    with pytest.raises(B2DailyCapError):
        read_object_text("pinterest/ledger/ancient.jsonl", client=client, bucket="bucket")
    assert client.gets == 3
    assert delays == [1.0, 2.0]


def test_class_c_limit_from_env(
    budget: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    del budget
    monkeypatch.setenv("B2_DAILY_CLASS_C", "0")
    client = _Client()
    with caplog.at_level(logging.WARNING, logger="agents.media.b2_budget"):
        with pytest.raises(B2DailyCapError):
            list_object_keys("any/", client=client, bucket="bucket")
        with pytest.raises(B2DailyCapError):
            list_object_keys("other/", client=client, bucket="bucket")
    assert client.lists == 0
    assert len([record for record in caplog.records if "Class C" in record.message]) == 1


def test_defaults_stay_at_half_the_free_allowance(budget: Path) -> None:
    del budget
    from agents.media.b2_budget import class_b_limit, class_c_limit, download_limit_bytes

    assert download_limit_bytes() == 500 * 1024 * 1024
    assert class_b_limit() == 1500
    assert class_c_limit() == 1500


def test_env_cannot_raise_budgets_above_the_free_allowance(
    budget: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del budget
    from agents.media.b2_budget import (
        charge,
        class_b_limit,
        class_c_limit,
        download_limit_bytes,
    )

    monkeypatch.setenv("B2_DAILY_DOWNLOAD_MB", "5000")
    monkeypatch.setenv("B2_DAILY_CLASS_B", "9000")
    monkeypatch.setenv("B2_DAILY_CLASS_C", "9000")
    assert download_limit_bytes() == 1000 * 1024 * 1024
    assert class_b_limit() == 2500
    assert class_c_limit() == 2500
    charge(class_b=2500, class_c=2500)
    with pytest.raises(B2DailyCapError):
        charge(class_b=1)
    with pytest.raises(B2DailyCapError):
        charge(class_c=1)


def test_clamped_download_cap_refuses_the_fetch(
    budget: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    del budget
    monkeypatch.setenv("B2_DAILY_DOWNLOAD_MB", "4000")
    client = _Client({"clips/huge.jpg": b"x"})
    from agents.media.b2_budget import client_token, store_listing

    store_listing(
        client_token(client),
        "bucket",
        "clips/",
        ["clips/huge.jpg"],
        {"clips/huge.jpg": 1000 * 1024 * 1024 + 1},
    )
    with pytest.raises(B2DailyCapError):
        download_object("clips/huge.jpg", tmp_path / "huge.jpg", client=client, bucket="bucket")
    assert client.downloads == []


def test_lower_env_override_is_kept(budget: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    del budget
    from agents.media.b2_budget import class_b_limit, download_limit_mb

    monkeypatch.setenv("B2_DAILY_DOWNLOAD_MB", "100")
    monkeypatch.setenv("B2_DAILY_CLASS_B", "10")
    assert download_limit_mb() == 100
    assert class_b_limit() == 10


def test_usage_lock_excludes_concurrent_writers(budget: Path) -> None:
    import threading
    import time

    from agents.media.b2_budget import _usage_file_lock

    del budget
    order: list[str] = []

    def worker(label: str) -> None:
        with _usage_file_lock():
            order.append(f"in-{label}")
            time.sleep(0.05)
            order.append(f"out-{label}")

    threads = [threading.Thread(target=worker, args=(str(i),)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(order) == 4
    assert order[0].startswith("in-")
    assert order[1] == "out-" + order[0].removeprefix("in-")
    assert order[2].startswith("in-")
    assert order[3] == "out-" + order[2].removeprefix("in-")


def test_processes_share_one_usage_file(budget: Path) -> None:
    import os
    import subprocess
    import sys

    path = budget / "usage.json"
    code = (
        "import os\n"
        "from agents.media.b2_budget import B2DailyCapError, charge\n"
        "try:\n"
        "    charge(class_c=2)\n"
        "    print('ok')\n"
        "except B2DailyCapError:\n"
        "    print('capped')\n"
    )
    env = os.environ.copy()
    env["B2_USAGE_PATH"] = str(path)
    env["B2_DAILY_CLASS_C"] = "2"
    env.pop("B2_DAILY_CLASS_B", None)
    env.pop("B2_DAILY_DOWNLOAD_MB", None)
    root = str(Path(__file__).resolve().parents[1])
    first = subprocess.run(
        [sys.executable, "-c", code],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    second = subprocess.run(
        [sys.executable, "-c", code],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert first.stdout.strip() == "ok"
    assert second.stdout.strip() == "capped"
    state = json.loads(path.read_text(encoding="utf-8"))
    assert state["class_c"] == 2


def test_backblaze_endpoint_is_not_treated_as_runpod() -> None:
    from agents.media.b2_budget import is_backblaze_endpoint

    assert is_backblaze_endpoint("https://s3.us-east-005.backblazeb2.com")
    assert is_backblaze_endpoint("https://s3.eu-central-003.backblaze.com")
    assert not is_backblaze_endpoint("https://s3api-eu-ro-1.runpod.io")
    assert not is_backblaze_endpoint("")


def test_runpod_s3_refuses_backblaze_before_creating_a_client() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "core" / "remote_gpu_manager.py"
    ).read_text(encoding="utf-8")
    function = source.split("def _get_runpod_s3_client", 1)[1].split("\ndef ", 1)[0]
    refuse_at = function.index("_refuse_backblaze_endpoint")
    client_at = function.index("boto3.client")
    assert refuse_at < client_at
