# -*- coding: utf-8 -*-
"""VM Pinterest path: image cards, article copy, B2 on demand, dry-run."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest
import requests
from PIL import Image

from agents.media.b2_client import media_bucket_name, resolve_b2_bucket_name
from agents.posting.pinterest_engine.inventory import MasterInventory
from agents.posting.pinterest_engine.scheduler import (
    _entry_is_video_only,
    _title_is_filename,
)
from omni.accounts import pinterest_account_env, remember_rotated_token
from omni.adapters.pinterest import PinterestAccountPublisher
from omni.models import Account, Content
from omni.pinterest_accounts import (
    account_blockers,
    active_pinterest_accounts,
    load_pinterest_accounts,
)
from omni.pinterest_drip import PublishedPins, publish_account
from omni.publisher import MemoryIdempotencyStore
from omni.worker import run_pinterest_drip

_SITE = "https://ancientknowledge.eokviral.com"
_IMAGE_KEY = "ancient_knowledge/gobekli-tepe-pillars.jpg"
_SECOND_KEY = "ancient_knowledge/nazca-lines.jpg"
_VIDEO_KEY = "ancient_knowledge/reel_gobekli_tepe_v02.mp4"
_ARTICLE = f"{_SITE}/gobekli-tepe-pillars"


class _Body:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


class _Response:
    def __init__(self, status_code: int, payload) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)
        self.content = self.text.encode()

    def json(self):
        return self._payload


class FakeS3:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects
        self.downloads: list[str] = []
        self.deletes: list[dict] = []
        self.buckets: list[str] = []

    def list_objects_v2(self, **kwargs):
        self.buckets.append(kwargs.get("Bucket", ""))
        prefix = kwargs.get("Prefix") or ""
        contents = [{"Key": key} for key in self.objects if key.startswith(prefix)]
        return {"Contents": contents, "IsTruncated": False}

    def download_file(self, bucket, key, filename):
        self.downloads.append(key)
        self.buckets.append(bucket)
        path = Path(filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.objects[key])

    def get_object(self, **kwargs):
        key = kwargs["Key"]
        if key not in self.objects:
            raise FileNotFoundError(key)
        return {"Body": _Body(self.objects[key])}

    def delete_object(self, **kwargs):
        self.deletes.append(kwargs)
        raise AssertionError("B2 objects must not be deleted")


def _jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (32, 48), (40, 30, 20)).save(buf, format="JPEG")
    return buf.getvalue()


def _sidecar(**extra) -> bytes:
    payload = {
        "slug": "gobekli-tepe-pillars",
        "topic": "The carved pillars of Gobekli Tepe",
        "humanized_caption": (
            "A temple older than pottery, cut into a hill in southeastern Turkey."
        ),
        "pinterest_title": "Carved Pillars of Gobekli Tepe",
        "pinterest_caption": (
            "A temple older than pottery, cut into a hill in southeastern Turkey."
        ),
        "visual_hook": "STONES BEFORE POTTERY",
    }
    payload.update(extra)
    return json.dumps(payload).encode()


def _ancient() -> Account:
    return next(
        account
        for account in active_pinterest_accounts()
        if account.account_id == "ancient_knowledge"
    )


def _bind_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PINTEREST_ACCESS_TOKEN_ANCIENT_KNOWLEDGE", "tok-ancient")
    monkeypatch.setenv("PINTEREST_BOARD_ID_ANCIENT_KNOWLEDGE", "board-ancient")
    monkeypatch.setenv("PINTEREST_REFRESH_TOKEN_ANCIENT_KNOWLEDGE", "refresh-ancient")
    monkeypatch.setenv("PINTEREST_APP_ID_ANCIENT_KNOWLEDGE", "app-ancient")
    monkeypatch.setenv("PINTEREST_APP_SECRET_ANCIENT_KNOWLEDGE", "secret-ancient")
    monkeypatch.delenv("B2_BUCKET_NAME", raising=False)


def _capture_posts(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    captured: list[dict] = []
    dumps = json.dumps
    loads = json.loads

    def fake_post(url, data=None, headers=None, json=None, timeout=None, **kwargs):  # noqa: A002
        del json, timeout, kwargs
        if "deepseek" in url:
            assert "Bearer " in (headers or {}).get("Authorization", "")
            reply = {
                "pinterest_title": "Carved Pillars Older Than Pottery",
                "pinterest_caption": "Researchers map pillars raised before farming.",
                "visual_hook": "BEFORE FARMING",
            }
            return _Response(200, {"choices": [{"message": {"content": dumps(reply)}}]})
        captured.append(
            {
                "url": url,
                "body": loads(data),
                "auth": (headers or {}).get("Authorization"),
            }
        )
        return _Response(201, {"id": f"pin_{len(captured)}"})

    monkeypatch.setattr(requests, "post", fake_post)
    return captured


def test_accounts_yaml_scales_without_code_and_marks_anna_todo(tmp_path: Path) -> None:
    accounts = load_pinterest_accounts()
    by_id = {account.account_id: account for account in accounts}
    ancient = by_id["ancient_knowledge"]
    anna = by_id["anna_protocol"]
    assert ancient.extra["site_url"] == _SITE
    assert ancient.extra["b2_prefix"] == "ancient_knowledge/"
    assert ancient.token_env == "PINTEREST_ACCESS_TOKEN_ANCIENT_KNOWLEDGE"
    assert ancient.extra["app_id_env"] == "PINTEREST_APP_ID_ANCIENT_KNOWLEDGE"
    assert ancient.extra["app_secret_env"] == "PINTEREST_APP_SECRET_ANCIENT_KNOWLEDGE"
    assert ancient.refresh_token_env == "PINTEREST_REFRESH_TOKEN_ANCIENT_KNOWLEDGE"
    assert ancient.board_name == "Ancient Knowledge"
    assert account_blockers(ancient) == []

    assert anna.extra["display_name"] == "Anna's Garden"
    assert anna.extra["site_url"] == "TODO"
    assert anna.board_name == "TODO"
    assert anna.extra["b2_prefix"] == "anna_protocol/"
    assert "site_url" in account_blockers(anna)
    assert "board" in account_blockers(anna)

    rows = []
    for index in range(11):
        rows.append(
            {
                "channel_id": f"garden_{index}",
                "active": True,
                "niche": "herbs",
                "site_url": f"https://example.test/{index}",
                "b2_prefix": f"garden_{index}/",
                "board_name": "Garden",
                "access_token_env": f"PINTEREST_ACCESS_TOKEN_GARDEN_{index}",
                "refresh_token_env": f"PINTEREST_REFRESH_TOKEN_GARDEN_{index}",
                "app_id_env": f"PINTEREST_APP_ID_GARDEN_{index}",
                "app_secret_env": f"PINTEREST_APP_SECRET_GARDEN_{index}",
            }
        )
    import yaml

    loaded_path = tmp_path / "pinterest_accounts_scale.yaml"
    loaded_path.write_text(yaml.safe_dump({"accounts": rows}), encoding="utf-8")
    scaled = load_pinterest_accounts(loaded_path)
    assert len(scaled) == 11
    assert scaled[10].extra["b2_prefix"] == "garden_10/"


def test_bucket_defaults_when_name_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("B2_BUCKET_NAME", "")
    assert media_bucket_name() == "MediaupscaleStorage"
    with pytest.raises(ValueError, match="B2_BUCKET_NAME"):
        resolve_b2_bucket_name()


def test_image_pin_uses_article_not_filename(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("OUTPUT_PATH", str(tmp_path))
    _bind_tokens(monkeypatch)
    caplog.set_level("INFO")
    sidecar_key = "ancient_knowledge/gobekli-tepe-pillars.json"
    client = FakeS3(
        {
            _IMAGE_KEY: _jpeg(),
            sidecar_key: _sidecar(),
            _SECOND_KEY: _jpeg(),
            _VIDEO_KEY: b"video-bytes",
        }
    )
    captured = _capture_posts(monkeypatch)
    stats = publish_account(_ancient(), pins=1, client=client)
    assert stats["published"] == 1
    assert stats["errors"] == 0
    assert client.downloads == [_IMAGE_KEY]
    assert _VIDEO_KEY not in client.downloads
    assert _SECOND_KEY not in client.downloads
    assert client.deletes == []
    assert client.buckets
    assert set(client.buckets) == {"MediaupscaleStorage"}
    assert captured[0]["url"] == "https://api.pinterest.com/v5/pins"
    assert captured[0]["auth"] == "Bearer tok-ancient"
    body = captured[0]["body"]
    assert body["media_source"]["source_type"] == "image_base64"
    assert body["board_id"] == "board-ancient"
    assert body["title"] == "Carved Pillars of Gobekli Tepe"
    assert body["link"] == _ARTICLE
    assert "gobekli tepe" not in body["title"].lower() or "Carved" in body["title"]
    assert "reel_" not in body["title"].lower()
    assert "#AncientKnowledge" in body["description"]
    assert "older than pottery" in body["description"]
    assert "video_id" not in json.dumps(body["media_source"])
    assert not list((tmp_path / "ancient_knowledge" / "_tmp" / "pinterest").glob("*"))
    ledger = PublishedPins()
    rows = ledger._load()
    assert len(rows) == 1
    assert rows[0].account == "ancient_knowledge"
    assert rows[0].b2_object == _IMAGE_KEY
    assert rows[0].article == _ARTICLE
    assert rows[0].pin_id == "pin_1"
    assert "tok-ancient" not in caplog.text
    assert "secret-ancient" not in caplog.text

    again = publish_account(_ancient(), pins=1, client=client)
    assert again["published"] == 0
    assert client.downloads == [_IMAGE_KEY]
    assert len(captured) == 1


def test_deepseek_title_is_not_the_filename(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OUTPUT_PATH", str(tmp_path))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test-key")
    _bind_tokens(monkeypatch)
    key = "ancient_knowledge/reel_hidden_chamber_v03.jpg"
    sidecar = _sidecar(
        slug="hidden-chamber",
        topic="",
        pinterest_title="",
        pinterest_caption="",
        visual_hook="",
        humanized_caption="A sealed chamber under the hill still holds painted stone.",
        article_url=f"{_SITE}/hidden-chamber",
    )
    client = FakeS3({key: _jpeg(), "ancient_knowledge/reel_hidden_chamber_v03.json": sidecar})
    captured = _capture_posts(monkeypatch)
    stats = publish_account(_ancient(), pins=1, client=client)
    assert stats["published"] == 1
    assert captured[0]["body"]["title"] == "Carved Pillars Older Than Pottery"
    assert "hidden chamber" not in captured[0]["body"]["title"].lower()
    assert "reel_" not in captured[0]["body"]["title"].lower()
    assert captured[0]["body"]["link"] == f"{_SITE}/hidden-chamber"
    assert "deepseek-test-key" not in json.dumps(captured)


def test_wordpress_article_supplies_the_link(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OUTPUT_PATH", str(tmp_path))
    _bind_tokens(monkeypatch)
    key = "ancient_knowledge/antikythera-mechanism.jpg"
    client = FakeS3({key: _jpeg()})

    def fake_get(url, params=None, timeout=None, **_kwargs):
        assert url == f"{_SITE}/wp-json/wp/v2/posts"
        assert params["slug"] == "antikythera-mechanism"
        return _Response(
            200,
            [
                {
                    "slug": "antikythera-mechanism",
                    "link": f"{_SITE}/antikythera-mechanism",
                    "title": {"rendered": "The Antikythera Mechanism"},
                    "excerpt": {"rendered": "<p>A geared device from a Hellenistic wreck.</p>"},
                    "content": {"rendered": ""},
                }
            ],
        )

    monkeypatch.setattr(requests, "get", fake_get)
    captured = _capture_posts(monkeypatch)
    stats = publish_account(_ancient(), pins=1, client=client)
    assert stats["published"] == 1
    assert captured[0]["body"]["link"] == f"{_SITE}/antikythera-mechanism"
    assert captured[0]["body"]["title"].startswith("The Antikythera Mechanism")
    assert client.downloads == [key]


def test_dry_run_does_not_post_or_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OUTPUT_PATH", str(tmp_path))
    _bind_tokens(monkeypatch)
    client = FakeS3(
        {
            _IMAGE_KEY: _jpeg(),
            "ancient_knowledge/gobekli-tepe-pillars.json": _sidecar(),
        }
    )
    captured = _capture_posts(monkeypatch)
    stats = publish_account(_ancient(), pins=1, dry_run=True, client=client)
    assert stats["dry_run"] is True
    assert stats["published"] == 1
    assert captured == []
    assert client.downloads == [_IMAGE_KEY]
    assert client.deletes == []
    assert not PublishedPins().path.is_file()
    assert not list((tmp_path / "ancient_knowledge" / "_tmp" / "pinterest").glob("*"))

    live = publish_account(_ancient(), pins=1, client=client)
    assert live["published"] == 1
    assert len(captured) == 1


def test_anna_todo_does_not_touch_b2(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OUTPUT_PATH", str(tmp_path))
    anna = next(
        account
        for account in active_pinterest_accounts()
        if account.account_id == "anna_protocol"
    )
    client = FakeS3({"anna_protocol/flower.jpg": _jpeg()})
    stats = publish_account(anna, pins=1, client=client)
    assert stats["published"] == 0
    assert "site_url" in stats["skipped_reason"]
    assert client.downloads == []


def test_inventory_does_not_title_clips_from_filenames(tmp_path: Path) -> None:
    library = tmp_path / "library"
    library.mkdir()
    (library / "post_one.json").write_text(
        json.dumps(
            {
                "topic": "The pillars of Gobekli Tepe",
                "humanized_caption": "A temple older than pottery.",
                "subject_slug": "gobekli",
                "variant_index": 0,
            }
        ),
        encoding="utf-8",
    )
    clips = tmp_path / "clips"
    clips.mkdir()
    (clips / "reel_gobekli_tepe_v02.mp4").write_bytes(b"not-a-real-video")
    data = MasterInventory(outputs_dir=tmp_path).build(use_ai=False, dry_run=True)
    assert [entry["post_id"] for entry in data["entries"]] == ["post_one"]
    assert all(entry.get("media_kind") != "video" for entry in data["entries"])
    title = data["entries"][0]["pinterest_title"]
    assert "reel_" not in title.lower()
    assert "gobekli" in title.lower()


def test_scheduler_skips_video_entries_and_filename_titles() -> None:
    video = {
        "media_kind": "video",
        "local_video_path": "clips/reel_stones_v01.mp4",
        "pinterest_title": "gobekli tepe",
    }
    image = {
        "media_kind": "image",
        "local_image_path": "library/pillars.jpg",
        "pinterest_title": "Carved Pillars of Gobekli Tepe",
    }
    assert _entry_is_video_only(video) is True
    assert _entry_is_video_only(image) is False
    assert _title_is_filename("gobekli tepe", "reel_gobekli_tepe_v02.mp4") is True
    assert _title_is_filename(image["pinterest_title"], "reel_gobekli_tepe_v02.mp4") is False


def test_adapter_refuses_video_pins() -> None:
    publisher = PinterestAccountPublisher(store=MemoryIdempotencyStore())
    result = publisher.publish(
        Content(
            idempotency_key="video-1",
            title="reel stones",
            media_path="clips/reel_stones_v01.mp4",
            media_bytes=b"not-posted",
        ),
        _ancient(),
    )
    assert result.ok is False
    assert result.error == "video pins are disabled"


def test_worker_drip_delegates_to_image_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def fake(account, *, pins, dry_run):
        seen["id"] = account.account_id
        seen["pins"] = pins
        seen["dry_run"] = dry_run
        return {"published": 0, "errors": 0}

    monkeypatch.setattr("omni.pinterest_drip.publish_account", fake)

    def boom(self):
        raise AssertionError("video scheduler must not run")

    monkeypatch.setattr(
        "agents.posting.pinterest_engine.scheduler.PinterestScheduler.run",
        boom,
    )
    stats = run_pinterest_drip(_ancient(), dry_run=True)
    assert stats["published"] == 0
    assert seen == {"id": "ancient_knowledge", "pins": 1, "dry_run": True}


def test_app_credentials_overlay_and_refresh_writeback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("INFO")
    account = _ancient()
    monkeypatch.setenv("PINTEREST_APP_ID_ANCIENT_KNOWLEDGE", "app-ancient")
    monkeypatch.setenv("PINTEREST_APP_SECRET_ANCIENT_KNOWLEDGE", "secret-ancient")
    with pinterest_account_env(account):
        assert os.environ["PINTEREST_APP_ID"] == "app-ancient"
        assert os.environ["PINTEREST_APP_SECRET"] == "secret-ancient"
    assert "secret-ancient" not in caplog.text

    root = tmp_path / "repo"
    channel = root / "channels_env" / "ancient_knowledge"
    channel.mkdir(parents=True)
    env_file = channel / ".env"
    env_file.write_text("PINTEREST_ACCESS_TOKEN=old\n", encoding="utf-8")
    root_env = root / ".env"
    root_env.write_text("PINTEREST_ACCESS_TOKEN_ANCIENT_KNOWLEDGE=old\n", encoding="utf-8")
    monkeypatch.setattr("omni.accounts.repo_root", lambda: root)
    monkeypatch.setenv("PINTEREST_ACCESS_TOKEN", "rotated-access")
    monkeypatch.setenv("_OMNI_APPLIED_PINTEREST_TOKEN", "old-access")
    monkeypatch.setenv("PINTEREST_REFRESH_TOKEN", "rotated-refresh")
    monkeypatch.setenv("_OMNI_APPLIED_PINTEREST_REFRESH", "old-refresh")
    remember_rotated_token(account, env_file)
    channel_text = env_file.read_text(encoding="utf-8")
    root_text = root_env.read_text(encoding="utf-8")
    assert "PINTEREST_ACCESS_TOKEN_ANCIENT_KNOWLEDGE=rotated-access" in channel_text
    assert "PINTEREST_REFRESH_TOKEN_ANCIENT_KNOWLEDGE=rotated-refresh" in channel_text
    assert "PINTEREST_ACCESS_TOKEN_ANCIENT_KNOWLEDGE=rotated-access" in root_text
    assert "rotated-access" not in caplog.text


def test_publisher_refresh_writes_env_without_logging_tokens(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import agents.posting.pinterest_engine.publisher as publisher_mod
    from agents.posting.pinterest_engine import config as cfg

    caplog.set_level("DEBUG")
    env_path = tmp_path / ".env"
    env_path.write_text("PINTEREST_ACCESS_TOKEN=old\n", encoding="utf-8")
    monkeypatch.setattr(cfg, "DOTENV_PATH", env_path)
    monkeypatch.setattr(cfg, "DOTENV_LOADED_FROM_FILE", True)
    monkeypatch.setenv("PINTEREST_REFRESH_TOKEN", "refresh-old")
    monkeypatch.setenv("PINTEREST_APP_ID", "app-id")
    monkeypatch.setenv("PINTEREST_APP_SECRET", "app-secret")

    class _UrlResp:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def read(self):
            return json.dumps(
                {
                    "access_token": "super-secret-access",
                    "refresh_token": "super-secret-refresh",
                }
            ).encode()

    monkeypatch.setattr(publisher_mod.urllib.request, "urlopen", lambda *_a, **_k: _UrlResp())
    publisher = publisher_mod.PinterestPublisher(
        access_token="old-token",
        board_id="board-1",
        require_board=False,
    )
    assert publisher._try_refresh() is True
    text = env_path.read_text(encoding="utf-8")
    assert "PINTEREST_ACCESS_TOKEN=super-secret-access" in text
    assert "PINTEREST_REFRESH_TOKEN=super-secret-refresh" in text
    assert "super-secret-access" not in caplog.text
    assert "super-secret-refresh" not in caplog.text
    assert "app-secret" not in caplog.text
