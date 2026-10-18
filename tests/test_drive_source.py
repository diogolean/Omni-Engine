# -*- coding: utf-8 -*-
"""Google Drive library source: list, size, one download, no deletes."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from agents.media import content_source, drive_source
from agents.media.b2_budget import reset_caches as reset_b2_caches
from agents.media.drive_source import (
    READONLY_SCOPE,
    DriveQuotaError,
    download_object,
    list_object_keys,
    object_size,
)
from agents.posting.pinterest_engine.library_source import (
    ensure_local_library,
    materialize_image,
)
from agents.posting.youtube_publisher import resolve_upload_video

_FOLDER = "application/vnd.google-apps.folder"
_WIN_IMAGE = (
    r"G:\My Drive\Z sosFiles\Z_act\@ NETWORK"
    r"\@MEDIAUPSCALE_FACTORY_DYNAMIC_CONTENT"
    r"\Unified Multi-Page Factory\outputs"
    r"\ancient_knowledge\library\images\one.jpg"
)
_WIN_CLIP = (
    r"G:\My Drive\Z sosFiles\Z_act\@ NETWORK"
    r"\@MEDIAUPSCALE_FACTORY_DYNAMIC_CONTENT"
    r"\Unified Multi-Page Factory\outputs"
    r"\ancient_knowledge\clips\reel.mp4"
)
_WIN_AIWAKE = (
    r"G:\My Drive\Z sosFiles\Z_act\@ NETWORK"
    r"\@MEDIAUPSCALE_FACTORY_DYNAMIC_CONTENT"
    r"\Unified Multi-Page Factory\outputs"
    r"\aiwake\clips\reel.mp4"
)


class _Exec:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def execute(self) -> dict:
        return self._payload


class _Media:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def execute_media(self) -> bytes:
        return self._data


class _Body:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


class QuotaError(Exception):
    def __init__(self) -> None:
        super().__init__("rate limit")
        self.resp = type("Resp", (), {"status": 429})()
        self.content = b'{"error":{"errors":[{"reason":"userRateLimitExceeded"}]}}'


class Denied(Exception):
    def __init__(self) -> None:
        super().__init__("forbidden")
        self.resp = type("Resp", (), {"status": 403})()
        self.content = b'{"error":{"errors":[{"reason":"forbidden"}]}}'


def _query_name(query: str) -> str | None:
    marker = "name = '"
    if marker not in query:
        return None
    raw = query.split(marker, 1)[1]
    return raw.split("'", 1)[0].replace("\\'", "'")


def _listed_parents(fake: FakeDrive) -> list[str]:
    parents: list[str] = []
    for name, kwargs in fake.calls:
        if name != "list":
            continue
        parents.append(str(kwargs.get("q") or "").split("'")[1])
    return parents


def _file(file_id: str, name: str, data: bytes, mime: str) -> dict:
    return {
        "id": file_id,
        "name": name,
        "mimeType": mime,
        "size": len(data),
        "data": data,
    }


def _folder(file_id: str, name: str) -> dict:
    return {"id": file_id, "name": name, "mimeType": _FOLDER}


_RECORD = b'{"post_id":"post_one","topic":"Stones","image_relative":"images/one.jpg"}\n'
_IMAGE = b"\xff\xd8\xffjpeg-bytes"
_VIDEO = b"mp4-bytes"


def _library_tree() -> dict:
    return {
        "lib-ak": [
            _file("json1", "post_one.json", _RECORD, "application/json"),
            _folder("imgdir", "images"),
        ],
        "imgdir": [_file("img1", "one.jpg", _IMAGE, "image/jpeg")],
    }


def _outputs_tree() -> dict:
    """Shared outputs folder: JSON lives in library/, the image lives in assets/."""
    record = (
        b'{"post_id":"post_one","topic":"Stones",'
        b'"image_relative":"assets/stone.jpg"}\n'
    )
    return {
        "outputs-root": [
            _folder("ak", "ancient_knowledge"),
            _folder("aw", "aiwake"),
            _folder("mm", "master_mei"),
        ],
        "ak": [
            _folder("ak-lib", "library"),
            _folder("ak-assets", "assets"),
        ],
        "ak-lib": [
            _file("json1", "post_one.json", record, "application/json"),
            _file("json2", "post_two.json", b'{"post_id":"post_two"}\n', "application/json"),
        ],
        "ak-assets": [_file("asset1", "stone.jpg", _IMAGE, "image/jpeg")],
        "aw": [_folder("aw-clips", "clips")],
        "aw-clips": [_file("awvid", "reel.mp4", _VIDEO, "video/mp4")],
        "mm": [_file("mm1", "note.txt", b"mei", "text/plain")],
    }


def _channel_tree() -> dict:
    return {
        "chan-ak": [
            _folder("lib", "library"),
            _folder("clips", "clips"),
        ],
        "lib": [_file("json1", "post_one.json", _RECORD, "application/json")],
        "clips": [_file("vid1", "reel.mp4", _VIDEO, "video/mp4")],
    }


class FakeDrive:
    def __init__(self, tree: dict) -> None:
        self.tree = tree
        self.calls: list[tuple[str, dict]] = []
        self.media: list[str] = []
        self.deleted: list[dict] = []

    def files(self) -> FakeDrive:
        return self

    def list(self, **kwargs):
        self.calls.append(("list", kwargs))
        query = str(kwargs.get("q") or "")
        parent = query.split("'")[1]
        name = _query_name(query)
        files = []
        for item in self.tree.get(parent, []):
            if name is not None and item["name"] != name:
                continue
            row = {
                "id": item["id"],
                "name": item["name"],
                "mimeType": item["mimeType"],
            }
            if item["mimeType"] != _FOLDER:
                row["size"] = item["size"]
            files.append(row)
        return _Exec({"files": files})

    def get_media(self, **kwargs):
        self.calls.append(("get_media", kwargs))
        file_id = kwargs["fileId"]
        self.media.append(file_id)
        return _Media(self._bytes(file_id))

    def delete(self, **kwargs):
        self.deleted.append(kwargs)
        raise AssertionError("Drive delete is not allowed")

    def _bytes(self, file_id: str) -> bytes:
        for items in self.tree.values():
            for item in items:
                if item["id"] == file_id:
                    return item["data"]
        raise KeyError(file_id)


class FlakyDrive(FakeDrive):
    def __init__(self, tree: dict, failures: int, exc: Exception) -> None:
        super().__init__(tree)
        self.left = failures
        self.exc = exc

    def list(self, **kwargs):
        if self.left > 0:
            self.left -= 1
            self.calls.append(("list", kwargs))
            raise self.exc
        return super().list(**kwargs)


class FakeB2:
    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self.objects = dict(objects or {})
        self.puts: list[str] = []
        self.deleted: list[str] = []
        self.lists = 0

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
        return {"Body": _Body(self.objects[kwargs["Key"]])}

    def download_file(self, _bucket, key, filename):
        Path(filename).write_bytes(self.objects[key])

    def put_object(self, **kwargs):
        self.puts.append(kwargs["Key"])
        self.objects[kwargs["Key"]] = bytes(kwargs.get("Body") or b"")
        return {}

    def delete_object(self, **kwargs):
        self.deleted.append(str(kwargs.get("Key") or ""))
        raise AssertionError("B2 delete is not allowed")


@pytest.fixture
def drive_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("DRIVE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("DRIVE_LIST_CACHE_TTL_SECONDS", "3600")
    monkeypatch.setenv("OUTPUT_PATH", str(tmp_path / "outputs"))
    sa = tmp_path / "sa.json"
    sa.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GOOGLE_DRIVE_SA_JSON", str(sa))
    monkeypatch.setenv("DRIVE_LIBRARY_FOLDER_ID_ANCIENT_KNOWLEDGE", "lib-ak")
    monkeypatch.delenv("LIBRARY_SOURCE", raising=False)
    drive_source.reset_caches()
    yield tmp_path
    drive_source.reset_caches()


def _b2_budget(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("B2_USAGE_PATH", str(tmp_path / "b2-usage.json"))
    monkeypatch.setenv("B2_DAILY_DOWNLOAD_MB", "100")
    monkeypatch.setenv("B2_DAILY_CLASS_B", "1000")
    monkeypatch.setenv("B2_DAILY_CLASS_C", "1000")
    monkeypatch.delenv("B2_ALLOW_VIDEO_DOWNLOAD", raising=False)
    reset_b2_caches()


def _assert_drive_flags(fake: FakeDrive) -> None:
    assert fake.calls
    for name, kwargs in fake.calls:
        if name == "list":
            assert kwargs["supportsAllDrives"] is True
            assert kwargs["includeItemsFromAllDrives"] is True
        if name == "get_media":
            assert kwargs["supportsAllDrives"] is True
    assert fake.deleted == []


def test_readonly_scope_and_channel_env_name() -> None:
    assert READONLY_SCOPE == "https://www.googleapis.com/auth/drive.readonly"
    assert (
        drive_source.env_name_for_channel("ancient_knowledge")
        == "DRIVE_LIBRARY_FOLDER_ID_ANCIENT_KNOWLEDGE"
    )


def test_service_account_errors_are_not_logged(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "sa.json"
    path.write_text('{"private_key": "SECRET-KEY-MATERIAL"}', encoding="utf-8")
    monkeypatch.setenv("GOOGLE_DRIVE_SA_JSON", str(path))

    def boom(_path: Path) -> None:
        raise RuntimeError("SECRET-KEY-MATERIAL leaked in the exception")

    monkeypatch.setattr(drive_source, "_load_credentials", boom)
    with caplog.at_level(logging.DEBUG):
        assert drive_source.build_drive_service() is None
    assert "SECRET-KEY-MATERIAL" not in caplog.text

    caplog.clear()
    monkeypatch.setenv("GOOGLE_DRIVE_SA_JSON", "SECRET-KEY-MATERIAL\nnot-a-path")
    with caplog.at_level(logging.DEBUG):
        assert drive_source.build_drive_service() is None
    assert "SECRET-KEY-MATERIAL" not in caplog.text


def test_folder_id_env_beats_channel_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = tmp_path / "config"
    config.mkdir()
    (config / "brandings.yaml").write_text(
        "brandings:\n  sample_channel:\n    drive_library_folder_id: folder-from-yaml\n",
        encoding="utf-8",
    )
    (config / "pinterest_accounts.yaml").write_text(
        "accounts:\n  - channel_id: sample_channel\n    drive_folder_id: folder-from-pins\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("DRIVE_LIBRARY_FOLDER_ID_SAMPLE_CHANNEL", raising=False)
    drive_source.reset_caches()
    assert (
        drive_source.folder_id_for_channel("sample_channel", root=tmp_path)
        == "folder-from-yaml"
    )
    monkeypatch.setenv("DRIVE_LIBRARY_FOLDER_ID_SAMPLE_CHANNEL", "folder-from-env")
    assert (
        drive_source.folder_id_for_channel("sample_channel", root=tmp_path)
        == "folder-from-env"
    )
    assert (
        drive_source.folder_id_for_channel(
            "sample_channel",
            override="folder-explicit",
            root=tmp_path,
        )
        == "folder-explicit"
    )


def test_list_size_and_one_download_use_shared_drive_flags(drive_env: Path) -> None:
    assert drive_env.is_dir()
    fake = FakeDrive(_library_tree())
    prefix = "ancient_knowledge/library/"
    keys = list_object_keys(prefix, client=fake)
    assert keys == [
        "ancient_knowledge/library/images/one.jpg",
        "ancient_knowledge/library/post_one.json",
    ]
    image_key = "ancient_knowledge/library/images/one.jpg"
    assert object_size(image_key, client=fake) == len(_IMAGE)
    assert fake.media == []

    dest = drive_env / "pulled.jpg"
    download_object(image_key, dest, client=fake)
    again = drive_env / "pulled-again.jpg"
    download_object(image_key, again, client=fake)
    assert dest.read_bytes() == _IMAGE
    assert again.read_bytes() == _IMAGE
    assert fake.media == ["img1"]
    _assert_drive_flags(fake)


def test_listing_cache_survives_memory_reset(
    drive_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert drive_env.is_dir()
    fake = FakeDrive(_library_tree())
    list_object_keys("ancient_knowledge/library/", client=fake)
    listed = [name for name, _kwargs in fake.calls if name == "list"]
    list_object_keys("ancient_knowledge/library/", client=fake)
    assert [name for name, _kwargs in fake.calls if name == "list"] == listed

    drive_source.reset_caches()
    list_object_keys("ancient_knowledge/library/", client=fake)
    assert [name for name, _kwargs in fake.calls if name == "list"] == listed

    monkeypatch.setenv("DRIVE_LIST_CACHE_TTL_SECONDS", "0")
    list_object_keys("ancient_knowledge/library/", client=fake)
    assert len([name for name, _kwargs in fake.calls if name == "list"]) > len(listed)


def test_channel_folder_keeps_clips_beside_library(
    drive_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DRIVE_LIBRARY_FOLDER_ID_ANCIENT_KNOWLEDGE", "chan-ak")
    fake = FakeDrive(_channel_tree())
    keys = list_object_keys("ancient_knowledge/", client=fake)
    assert "ancient_knowledge/library/post_one.json" in keys
    assert "ancient_knowledge/clips/reel.mp4" in keys
    _assert_drive_flags(fake)


def test_quota_backs_off_then_succeeds(
    drive_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert drive_env.is_dir()
    slept: list[float] = []
    monkeypatch.setattr(drive_source, "_sleep", lambda seconds: slept.append(seconds))
    fake = FlakyDrive(_library_tree(), failures=2, exc=QuotaError())
    keys = list_object_keys("ancient_knowledge/library/", client=fake)
    assert "ancient_knowledge/library/post_one.json" in keys
    assert slept == [1.0, 2.0]


def test_quota_exhausted_stops(drive_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert drive_env.is_dir()
    slept: list[float] = []
    monkeypatch.setattr(drive_source, "_sleep", lambda seconds: slept.append(seconds))
    fake = FlakyDrive(_library_tree(), failures=5, exc=QuotaError())
    with pytest.raises(DriveQuotaError):
        list_object_keys("ancient_knowledge/library/", client=fake)
    assert slept == [1.0, 2.0]


def test_permission_denied_is_not_retried(
    drive_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert drive_env.is_dir()
    slept: list[float] = []
    monkeypatch.setattr(drive_source, "_sleep", lambda seconds: slept.append(seconds))
    fake = FlakyDrive(_library_tree(), failures=5, exc=Denied())
    with pytest.raises(Denied):
        list_object_keys("ancient_knowledge/library/", client=fake)
    assert slept == []
    assert len(fake.calls) == 1


def test_source_order_prefers_drive_when_credentials_exist(
    drive_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert content_source.library_source_order() == ("drive", "local", "b2")
    monkeypatch.setenv("LIBRARY_SOURCE", "b2")
    assert content_source.library_source_order() == ("b2",)
    monkeypatch.setenv("LIBRARY_SOURCE", "local")
    assert content_source.library_source_order() == ("local",)
    monkeypatch.delenv("GOOGLE_DRIVE_SA_JSON")
    monkeypatch.delenv("LIBRARY_SOURCE")
    assert content_source.library_source_order() == ("local", "b2")


def test_windows_drive_path_maps_to_an_object_key() -> None:
    assert content_source.object_key_for_path(_WIN_IMAGE) == (
        "ancient_knowledge/library/images/one.jpg"
    )
    assert content_source.object_key_for_path(_WIN_CLIP, "ancient_knowledge") == (
        "ancient_knowledge/clips/reel.mp4"
    )


def test_library_sync_downloads_json_only(
    drive_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeDrive(_library_tree())
    b2 = FakeB2(
        {"ancient_knowledge/library/post_b2.json": b'{"post_id":"post_b2"}\n'}
    )
    monkeypatch.setattr(drive_source, "build_drive_service", lambda: fake)
    written = ensure_local_library(
        drive_env / "ancient",
        prefix="ancient_knowledge/library/",
        client=b2,
        bucket="bucket",
    )
    assert written == 1
    saved = drive_env / "ancient" / "library" / "post_one.json"
    assert json.loads(saved.read_text(encoding="utf-8"))["topic"] == "Stones"
    assert fake.media == ["json1"]
    assert b2.lists == 0
    assert ensure_local_library(drive_env / "ancient", prefix="ancient_knowledge/library/") == 0
    assert fake.media == ["json1"]


def test_explicit_b2_skips_drive(
    drive_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _b2_budget(monkeypatch, drive_env)
    monkeypatch.setenv("LIBRARY_SOURCE", "b2")
    fake = FakeDrive(_library_tree())
    body = b'{"post_id":"post_b2"}\n'
    b2 = FakeB2({"ancient_knowledge/library/post_b2.json": body})
    monkeypatch.setattr(drive_source, "build_drive_service", lambda: fake)
    written = ensure_local_library(
        drive_env / "ancient",
        prefix="ancient_knowledge/library/",
        client=b2,
        bucket="bucket",
    )
    assert written == 1
    assert fake.calls == []
    assert (drive_env / "ancient" / "library" / "post_b2.json").read_bytes() == body


def test_drive_only_does_not_fall_through_to_b2(
    drive_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LIBRARY_SOURCE", "drive")
    fake = FakeDrive({"lib-ak": []})
    b2 = FakeB2({"ancient_knowledge/library/post_b2.json": b"{}\n"})
    monkeypatch.setattr(drive_source, "build_drive_service", lambda: fake)
    written = ensure_local_library(
        drive_env / "empty",
        prefix="ancient_knowledge/library/",
        client=b2,
        bucket="bucket",
    )
    assert written == 0
    assert b2.lists == 0


def test_local_g_path_wins_over_drive(
    drive_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeDrive(_library_tree())
    monkeypatch.setattr(drive_source, "build_drive_service", lambda: fake)
    local = drive_env / "outputs" / "ancient_knowledge" / "library" / "images" / "one.jpg"
    local.parent.mkdir(parents=True)
    local.write_bytes(b"on-disk")
    path, delete_after = materialize_image(
        {"local_image_path": _WIN_IMAGE, "image_relative": "images/one.jpg"},
        prefix="ancient_knowledge/library/",
        channel="ancient_knowledge",
    )
    assert path == local
    assert delete_after is False
    assert fake.media == []


def test_publish_downloads_one_image(
    drive_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeDrive(_library_tree())
    monkeypatch.setattr(drive_source, "build_drive_service", lambda: fake)
    record = {"image_relative": "images/one.jpg", "post_id": "post_one"}
    path, delete_after = materialize_image(
        record,
        prefix="ancient_knowledge/library/",
        channel="ancient_knowledge",
    )
    assert delete_after is True
    assert path is not None and path.read_bytes() == _IMAGE
    _again, _flag = materialize_image(
        record,
        prefix="ancient_knowledge/library/",
        channel="ancient_knowledge",
    )
    assert fake.media == ["img1"]
    _assert_drive_flags(fake)


def test_youtube_reads_one_clip_from_drive(
    drive_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DRIVE_LIBRARY_FOLDER_ID_ANCIENT_KNOWLEDGE", "chan-ak")
    fake = FakeDrive(_channel_tree())
    monkeypatch.setattr(drive_source, "build_drive_service", lambda: fake)
    path = resolve_upload_video(_WIN_CLIP, "Ancient Knowledge")
    assert path.read_bytes() == _VIDEO
    assert fake.media == ["vid1"]
    cached = resolve_upload_video(_WIN_CLIP, "ancient_knowledge")
    assert cached.read_bytes() == _VIDEO
    assert fake.media == ["vid1"]


def test_youtube_keeps_an_existing_local_file(
    drive_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local = drive_env / "reel.mp4"
    local.write_bytes(b"local-video")
    fake = FakeDrive(_channel_tree())
    monkeypatch.setattr(drive_source, "build_drive_service", lambda: fake)
    assert resolve_upload_video(local, "ancient_knowledge") == local
    assert fake.calls == []


def test_b2_public_url_uploads_a_drive_file_once(
    drive_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _b2_budget(monkeypatch, drive_env)
    client = FakeB2()
    source = drive_env / "reel.mp4"
    source.write_bytes(_VIDEO)
    url = content_source.ensure_b2_public_url(
        source,
        key="reel.mp4",
        client=client,
        bucket="Media",
        content_type="video/mp4",
    )
    assert url == "https://Media.s3.us-east-005.backblazeb2.com/reel.mp4"
    assert "drive.google.com" not in url
    again = content_source.ensure_b2_public_url(
        source,
        key="reel.mp4",
        client=client,
        bucket="Media",
        content_type="video/mp4",
    )
    assert again == url
    assert client.puts == ["reel.mp4"]
    assert client.deleted == []


def _load_attach_b2_urls():
    """Import Post Planner without the Aiwake package init.

    ``channels_config.aiwake`` imports the debate orchestrator, which imports
    numpy. The validate job does not install numpy. Post Planner itself does not.
    """
    import sys
    import types

    name = "channels_config.aiwake"
    if name not in sys.modules:
        root = Path("channels_config/aiwake").resolve()
        package = types.ModuleType(name)
        package.__path__ = [str(root)]
        package.__package__ = name
        sys.modules[name] = package
    from channels_config.aiwake.tools.post_planner import attach_b2_urls

    return attach_b2_urls


def test_post_planner_uploads_a_drive_only_video(
    drive_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attach_b2_urls = _load_attach_b2_urls()

    _b2_budget(monkeypatch, drive_env)
    monkeypatch.setenv("DRIVE_LIBRARY_FOLDER_ID_AIWAKE", "chan-ak")
    fake = FakeDrive(_channel_tree())
    monkeypatch.setattr(drive_source, "build_drive_service", lambda: fake)
    client = FakeB2()
    entries = [{"video_path": _WIN_AIWAKE, "b2_url": ""}]
    uploaded = attach_b2_urls(entries, b2_client=client, bucket="Media")
    assert uploaded["reel.mp4"].startswith("https://Media.s3.us-east-005.backblazeb2.com/")
    assert "drive.google.com" not in entries[0]["media_url"]
    assert entries[0]["b2_url"] == entries[0]["media_url"]
    assert client.puts == ["reel.mp4"]
    assert client.deleted == []
    assert fake.media == ["vid1"]
    attach_b2_urls([{"video_path": _WIN_AIWAKE}], b2_client=client, bucket="Media")
    assert client.puts == ["reel.mp4"]
    assert fake.media == ["vid1"]


def test_outputs_root_lists_only_the_library_prefix(
    drive_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert drive_env.is_dir()
    monkeypatch.delenv("DRIVE_LIBRARY_FOLDER_ID_ANCIENT_KNOWLEDGE", raising=False)
    monkeypatch.setenv("DRIVE_OUTPUTS_FOLDER_ID", "outputs-root")
    fake = FakeDrive(_outputs_tree())
    keys = list_object_keys("ancient_knowledge/library/", client=fake)
    assert keys == [
        "ancient_knowledge/library/post_one.json",
        "ancient_knowledge/library/post_two.json",
    ]
    parents = _listed_parents(fake)
    assert "aw" not in parents
    assert "ak-assets" not in parents
    assert "mm" not in parents
    assert parents.count("outputs-root") == 1
    assert "ak-lib" in parents
    _assert_drive_flags(fake)


def test_outputs_root_resolves_an_asset_beside_the_library(
    drive_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DRIVE_LIBRARY_FOLDER_ID_ANCIENT_KNOWLEDGE", raising=False)
    monkeypatch.setenv("DRIVE_OUTPUTS_FOLDER_ID", "outputs-root")
    fake = FakeDrive(_outputs_tree())
    monkeypatch.setattr(drive_source, "build_drive_service", lambda: fake)
    windows_asset = (
        r"G:\My Drive\Z sosFiles\Z_act\@ NETWORK"
        r"\@MEDIAUPSCALE_FACTORY_DYNAMIC_CONTENT"
        r"\Unified Multi-Page Factory\outputs"
        r"\ancient_knowledge\assets\stone.jpg"
    )
    record = {
        "image_relative": "assets/stone.jpg",
        "image_path": "outputs/ancient_knowledge/assets/stone.jpg",
        "local_image_path": windows_asset,
    }
    path, delete_after = materialize_image(
        record,
        prefix="ancient_knowledge/library/",
        channel="ancient_knowledge",
    )
    assert delete_after is True
    assert path is not None and path.read_bytes() == _IMAGE
    assert "ak-lib" not in _listed_parents(fake)
    assert "aw" not in _listed_parents(fake)
    assert fake.media == ["asset1"]
    _assert_drive_flags(fake)


def test_image_refs_resolve_against_the_outputs_root() -> None:
    from agents.posting.pinterest_engine.library_source import image_object_key

    prefix = "ancient_knowledge/library/"
    assert image_object_key({"image_relative": "assets/stone.jpg"}, prefix) == (
        "ancient_knowledge/assets/stone.jpg"
    )
    assert image_object_key(
        {"image_relative": "outputs/ancient_knowledge/assets/stone.jpg"},
        prefix,
    ) == "ancient_knowledge/assets/stone.jpg"
    assert image_object_key({"image_relative": "images/one.jpg"}, prefix) == (
        "ancient_knowledge/library/images/one.jpg"
    )
    assert image_object_key(
        {"b2_image_key": "ancient_knowledge/library/images/one.jpg"},
        prefix,
    ) == "ancient_knowledge/library/images/one.jpg"
    assert content_source.object_key_for_path(
        "ancient_knowledge/clips/reel.mp4",
        "ancient_knowledge",
    ) == "ancient_knowledge/clips/reel.mp4"


def test_per_channel_folder_overrides_the_outputs_root(
    drive_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert drive_env.is_dir()
    monkeypatch.setenv("DRIVE_OUTPUTS_FOLDER_ID", "outputs-root")
    monkeypatch.setenv("DRIVE_LIBRARY_FOLDER_ID_ANCIENT_KNOWLEDGE", "chan-ak")
    fake = FakeDrive({**_outputs_tree(), **_channel_tree()})
    keys = list_object_keys("ancient_knowledge/library/", client=fake)
    assert keys == ["ancient_knowledge/library/post_one.json"]
    assert "outputs-root" not in _listed_parents(fake)


def test_modules_do_not_call_delete() -> None:
    drive_text = Path("agents/media/drive_source.py").read_text(encoding="utf-8")
    content_text = Path("agents/media/content_source.py").read_text(encoding="utf-8")
    assert ".delete(" not in drive_text
    assert "trash(" not in drive_text
    assert ".delete(" not in content_text
