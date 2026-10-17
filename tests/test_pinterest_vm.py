# -*- coding: utf-8 -*-
"""VM Pinterest path: image cards, article copy, B2 on demand, dry-run."""

from __future__ import annotations

import io
import json
import os
from dataclasses import replace
from pathlib import Path

import pytest
import requests
from PIL import Image, ImageDraw

from agents.media.b2_client import media_bucket_name, resolve_b2_bucket_name
from agents.posting.pinterest_engine.image_transformer import (
    _SERIF_CANDIDATES,
    PinTransformer,
    _find_font,
)
from agents.posting.pinterest_engine.inventory import (
    MasterInventory,
    _generate_deepseek,
    build_title_template,
    sanitize_pin_title,
    title_case_phrase,
)
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
from omni.pinterest_drip import (
    account_runs_here,
    fetch_wordpress_catalog,
    is_text_graphic,
    match_article_slug,
    publish_account,
)
from omni.publisher import MemoryIdempotencyStore
from omni.worker import run_pinterest_drip, run_scheduler

_SITE = "https://ancientknowledge.eokviral.com"
_IMAGE_KEY = "fbcards/2026-10-04/ancientknowl_10-04_2130_baalbek.jpg"
_SIDECAR_KEY = "fbcards/2026-10-04/ancientknowl_10-04_2130_baalbek.json"
_SECOND_KEY = "fbcards/2026-10-05/ancient_not_a_place.jpg"
_VIDEO_KEY = "fbcards/2026-10-04/ancient_clip.mp4"
_ROOT_VIDEO = "reel_gobekli_tepe_v02.mp4"
_UNMATCHED_KEY = "fbcards/2026-10-04/garden_roses.jpg"
_ARTICLE = f"{_SITE}/gobekli-tepe-pillars"
_SITE_SLUGS = [
    "atlantis",
    "baalbek",
    "crystal-skulls",
    "dendera-light",
    "easter-island-moai",
    "giants",
    "nazca",
    "oak-island",
    "paititi",
    "sumerian",
    "tartaria",
    "underwater-cities",
    "voynich",
]


class _Body:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


class _Response:
    def __init__(
        self,
        status_code: int,
        payload,
        *,
        content: bytes | None = None,
        headers: dict | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload) if payload is not None else ""
        self.content = content if content is not None else self.text.encode()
        self.headers = headers or {}

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

    def put_object(self, **kwargs):
        body = kwargs.get("Body") or b""
        if hasattr(body, "read"):
            body = body.read()
        self.objects[kwargs["Key"]] = bytes(body)
        return {}


def _jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (32, 48), (40, 30, 20)).save(buf, format="JPEG")
    return buf.getvalue()


def _photo() -> bytes:
    """A varied photograph, not a flat title card."""
    img = Image.new("RGB", (120, 180))
    pixels = img.load()
    for y in range(180):
        for x in range(120):
            if y < 60:
                pixels[x, y] = (30 + (x % 50), 80 + (y % 40), 150 + (x % 40))
            elif y < 120:
                pixels[x, y] = (170 + (x % 40), 60 + (y % 30), 30 + (x % 25))
            else:
                pixels[x, y] = (20 + (x % 30), 150 + (y % 40), 40 + (x % 20))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _title_graphic() -> bytes:
    """Dark teal field with gold circles, the site's featured title card."""
    img = Image.new("RGB", (120, 180), (16, 78, 86))
    draw = ImageDraw.Draw(img)
    draw.ellipse((28, 24, 92, 88), outline=(212, 168, 64), width=4)
    draw.ellipse((46, 100, 74, 128), outline=(212, 168, 64), width=3)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
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


def _ancient(*, featured: bool | None = None) -> Account:
    account = next(
        item
        for item in active_pinterest_accounts()
        if item.account_id == "ancient_knowledge"
    )
    if featured is None:
        return account
    extra = dict(account.extra)
    extra["use_featured_image"] = "true" if featured else "false"
    return replace(account, extra=extra)


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
        del json, timeout
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
                "verify": kwargs.get("verify", True),
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
    assert ancient.extra["b2_prefix"] == "ancient_knowledge/library/"
    assert ancient.extra["runner"] == "both"
    assert ancient.extra["b2_filename_pattern"] == ""
    assert ancient.extra["article_tls_verify"] == "false"
    assert ancient.extra["use_featured_image"] == "false"
    assert ancient.token_env == "PINTEREST_ACCESS_TOKEN_ANCIENT_KNOWLEDGE"
    assert ancient.extra["app_id_env"] == "PINTEREST_APP_ID_ANCIENT_KNOWLEDGE"
    assert ancient.extra["app_secret_env"] == "PINTEREST_APP_SECRET_ANCIENT_KNOWLEDGE"
    assert ancient.refresh_token_env == "PINTEREST_REFRESH_TOKEN_ANCIENT_KNOWLEDGE"
    assert ancient.board_name == "Ancient Knowledge"
    assert account_blockers(ancient) == []

    assert anna.extra["display_name"] == "Anna's Garden"
    assert anna.extra["site_url"] == "TODO"
    assert anna.board_name == "TODO"
    assert anna.extra["b2_prefix"] == "anna_protocol/library/"
    assert anna.extra["runner"] == "both"
    assert anna.extra["b2_filename_pattern"] == ""
    assert anna.extra["article_tls_verify"] == "true"
    assert anna.extra["use_featured_image"] == "true"
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


def test_title_case_decodes_apostrophe_entities() -> None:
    assert title_case_phrase("Baalbek&#8217;s trilithon") == "Baalbek's Trilithon"
    assert title_case_phrase("Baalbek&#8217;S") == "Baalbek's"
    assert "&#" not in title_case_phrase("Baalbek&#8217;s stones")


def test_slug_match_uses_site_posts_and_skips_dates() -> None:
    assert match_article_slug(_IMAGE_KEY, _SITE_SLUGS) == "baalbek"
    assert match_article_slug("ancient_crystal_skulls.jpg", _SITE_SLUGS) == "crystal-skulls"
    assert (
        match_article_slug("ancient_easter_island_moai.jpg", _SITE_SLUGS)
        == "easter-island-moai"
    )
    assert (
        match_article_slug("ancientknowl_underwater_cities.jpg", _SITE_SLUGS)
        == "underwater-cities"
    )
    assert match_article_slug("ancient_dendera_light.jpg", _SITE_SLUGS) == "dendera-light"
    assert match_article_slug("ancient_oak_island.jpg", _SITE_SLUGS) == "oak-island"
    assert match_article_slug("ancient_voynich.jpg", _SITE_SLUGS) == "voynich"
    assert match_article_slug("ancient_baalbec.jpg", _SITE_SLUGS) == "baalbek"
    assert match_article_slug("ancient_crystalsculls.jpg", _SITE_SLUGS) == "crystal-skulls"
    assert (
        match_article_slug("ancientknowl_underwatercity.jpg", _SITE_SLUGS)
        == "underwater-cities"
    )
    assert match_article_slug("ancient_skull.jpg", _SITE_SLUGS) == ""
    assert match_article_slug("giants-tomb-sardinia.jpg", _SITE_SLUGS) == ""
    assert match_article_slug("ancient_giants_tomb_sardinia.jpg", _SITE_SLUGS) == ""
    assert match_article_slug("ancient_giants.jpg", _SITE_SLUGS) == "giants"
    assert (
        match_article_slug("ancientknowl_10-04_2130_baalbek-trilithon.jpg", _SITE_SLUGS)
        == "baalbek"
    )
    assert match_article_slug("baalbek-trilithon.jpg", _SITE_SLUGS) == "baalbek"
    assert match_article_slug("ancient_nazca_lines.jpg", _SITE_SLUGS) == "nazca"
    assert match_article_slug("ancientknowl_10-04_2130.jpg", _SITE_SLUGS) == ""
    assert match_article_slug("ancientknowl.jpg", _SITE_SLUGS) == ""
    assert match_article_slug("random_flower.jpg", _SITE_SLUGS) == ""














def test_text_graphic_detector(tmp_path: Path) -> None:
    graphic = tmp_path / "title.jpg"
    photo = tmp_path / "photo.jpg"
    graphic.write_bytes(_title_graphic())
    photo.write_bytes(_photo())
    assert is_text_graphic(graphic) is True
    assert is_text_graphic(photo) is False


def test_article_lookup_logs_tls_mismatch(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def boom(*_args, **_kwargs):
        raise requests.exceptions.SSLError("hostname mismatch")

    monkeypatch.setattr(requests, "get", boom)
    caplog.set_level("ERROR")
    assert fetch_wordpress_catalog(_SITE, verify=True, account_id="ancient_knowledge") == []
    assert "*.web-hosting.com" in caplog.text
    assert "article_tls_verify" in caplog.text


def test_deepseek_retries_fenced_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test-key")
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
    calls: list[dict] = []
    dumps = json.dumps

    def fake_post(url, json=None, headers=None, timeout=None, **_kwargs):  # noqa: A002
        calls.append(json)
        assert "Bearer deepseek-test-key" == (headers or {}).get("Authorization")
        if len(calls) == 1:
            return _Response(200, {"choices": [{"message": {"content": "sure, here you go"}}]})
        fenced = (
            "```json\n"
            + dumps(
                {
                    "pinterest_title": "Baalbek&#8217;s Stones",
                    "pinterest_caption": "Stones at Baalbek&#8217;s quarry.",
                    "visual_hook": "BAALBEK&#8217;S STONES",
                }
            )
            + "\n```"
        )
        return _Response(200, {"choices": [{"message": {"content": fenced}}]})

    monkeypatch.setattr(requests, "post", fake_post)
    data = _generate_deepseek("Baalbek&#8217;s trilithon", "Quarry stones.", "", 0)
    assert calls[0]["response_format"] == {"type": "json_object"}
    assert len(calls) == 2
    assert data["pinterest_title"] == "Baalbek's Stones"
    assert "&#" not in data["pinterest_caption"]
    assert data["visual_hook"] == "BAALBEK'S STONES"
    assert "deepseek-test-key" not in json.dumps(data)


def test_deepseek_drops_response_format_when_unsupported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test-key")
    seen: list[bool] = []
    dumps = json.dumps

    def fake_post(url, json=None, headers=None, timeout=None, **_kwargs):  # noqa: A002
        seen.append("response_format" in (json or {}))
        if len(seen) == 1:
            return _Response(400, {"error": "unsupported"})
        body = dumps(
            {
                "pinterest_title": "Nazca Lines From Above",
                "pinterest_caption": "The desert figures are still visible.",
                "visual_hook": "LINES FROM ABOVE",
            }
        )
        return _Response(200, {"choices": [{"message": {"content": body}}]})

    monkeypatch.setattr(requests, "post", fake_post)
    data = _generate_deepseek("Nazca", "Desert figures.", "", 0)
    assert seen == [True, False]
    assert data["pinterest_title"] == "Nazca Lines From Above"


def _sample_photo(path: Path) -> None:
    photo = Image.new("RGB", (1200, 800))
    pixels = photo.load()
    for y in range(800):
        color = (240, 200, 20) if y < 400 else (20, 190, 220)
        for x in range(1200):
            pixels[x, y] = color
    photo.save(path, format="JPEG", quality=95)


def test_rendered_card_matches_the_original_template(tmp_path: Path) -> None:
    """Local and Docker share one renderer and the bundled Georgia substitute."""
    source = tmp_path / "baalbek.jpg"
    _sample_photo(source)
    record = {
        "local_image_path": str(source),
        "visual_hook": "BAALBEK&#8217;S STONES",
        "topic": "Baalbek",
        "subject_slug": "baalbek",
        "variant_index": 0,
    }
    local = PinTransformer(outputs_dir=tmp_path / "local", method="blurred_padding")
    docker = PinTransformer(outputs_dir=tmp_path / "docker", method="blurred_padding")
    local_bytes = local.get_pin_bytes(record)
    docker_bytes = docker.get_pin_bytes(record)
    assert local_bytes and docker_bytes
    assert local_bytes == docker_bytes

    card = Image.open(io.BytesIO(local_bytes)).convert("RGB")
    assert card.size == (1000, 1500)
    pixels = card.load()
    total = 0
    button = 0
    for y in range(1300, 1480, 4):
        for x in range(40, 960, 4):
            total += 1
            r, g, b = pixels[x, y]
            if r < 90 and g < 110 and b < 90:
                button += 1
    assert total > 0
    assert button / total > 0.8

    font = _find_font(_SERIF_CANDIDATES, 64)
    font_path = Path(getattr(font, "path", ""))
    assert font_path.name == "LiberationSerif-Bold.ttf"
    assert "Fonts" in font_path.parts and "LiberationSerif" in font_path.parts
    _left, top, _right, bottom = font.getbbox("BAALBEK")
    assert bottom - top >= 40


def test_scheduler_waits_before_the_first_publish(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []

    monkeypatch.setattr("omni.worker.interval_seconds", lambda rng=None: 12)

    def fake_sleep(seconds, stop):
        calls.append(("sleep", seconds))
        if calls.count(("sleep", seconds)) > 1:
            stop["flag"] = True

    def fake_cycle(*, dry_run: bool = False):
        calls.append(("cycle", dry_run))
        return []

    monkeypatch.setattr("omni.worker._sleep_until", fake_sleep)
    monkeypatch.setattr("omni.worker.run_cycle", fake_cycle)
    run_scheduler(once=False, dry_run=False)
    assert calls == [("sleep", 12), ("cycle", False), ("sleep", 12)]


def test_once_dry_run_does_not_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    def _no_interval(rng=None):
        raise AssertionError("dry-run once must not sleep before publishing")

    cycles: list[bool] = []
    monkeypatch.setattr("omni.worker.interval_seconds", _no_interval)
    monkeypatch.setattr(
        "omni.worker.run_cycle",
        lambda *, dry_run=False: cycles.append(dry_run) or [],
    )
    run_scheduler(once=True, dry_run=True)
    assert cycles == [True]


def test_pin_theme_copy_drives_the_click_and_does_not_relink(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Hook, CTA, domain, and description come from pin_theme. Publish only creates pins."""
    import inspect

    from PIL import ImageDraw

    from agents.posting.pinterest_engine import config as cfg
    from agents.posting.pinterest_engine import publisher as publisher_mod
    from agents.posting.pinterest_engine.pin_copy import (
        build_pin_description,
        keyword_first_title,
        pin_alt_text,
    )

    cfg.configure(channel_id="ancient_knowledge", inventory_dir=tmp_path)
    source = tmp_path / "stones.jpg"
    _sample_photo(source)
    record = {
        "local_image_path": str(source),
        "topic": "Baalbek trilithon",
        "pinterest_title": "Hidden truth you won't believe",
        "subject_slug": "baalbek",
        "variant_index": 0,
        "target_url": f"{_SITE}/baalbek",
    }
    drawn: list[tuple[int, str]] = []
    original = ImageDraw.ImageDraw.text

    def spy(self, xy, text, *args, **kwargs):
        drawn.append((int(xy[0]), str(text)))
        return original(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", spy)
    raw = PinTransformer(outputs_dir=tmp_path / "pins").get_pin_bytes(record)
    assert raw
    blob = " ".join(text for _x, text in drawn).upper()
    assert "WHAT THE RECORDS LEAVE OUT ABOUT BAALBEK" in blob
    assert "READ THE FULL STORY ->" in blob
    assert "ANCIENTKNOWLEDGE.EOKVIRAL.COM" in blob
    assert "FOLLOW ANCIENT KNOWLEDGE" not in blob
    domain_x = [x for x, text in drawn if "eokviral.com" in text.lower()]
    assert domain_x and max(domain_x) < 80

    title = keyword_first_title(record["pinterest_title"], record["topic"])
    assert title.lower().startswith("baalbek")
    assert len(title) <= 100
    record["pinterest_title"] = title
    description = build_pin_description(record)
    assert description.lower().startswith("baalbek")
    assert description.endswith(f"Read the full article at {_SITE}/baalbek")
    assert len(description) <= 220
    assert "Practical Guide" not in description
    assert pin_alt_text(record) == title

    publish_src = inspect.getsource(publisher_mod.PinterestPublisher.publish)
    post_src = inspect.getsource(publisher_mod.PinterestPublisher._post_with_retry)
    assert "requests.patch" not in publish_src.lower()
    assert "requests.post" in post_src
    assert not hasattr(publisher_mod.PinterestPublisher, "update_pin")


def test_titles_drop_template_suffixes() -> None:
    assert "Practical Guide" not in build_title_template("Baalbek trilithon")
    assert sanitize_pin_title("Baalbek | Practical Guide") == "Baalbek"
    assert sanitize_pin_title("Tartaria — Key Insights") == "Tartaria"
    assert sanitize_pin_title("Nazca | Complete Overview") == "Nazca"


def test_runner_filter_skips_the_other_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PUBLISHER_MODE", "docker")
    account = _ancient()
    extra = dict(account.extra)
    extra["runner"] = "local"
    local_only = replace(account, extra=extra)
    assert account_runs_here(local_only) is False
    stats = publish_account(local_only, pins=1, client=FakeS3({}))
    assert stats["published"] == 0
    assert stats["skipped_reason"] == "runner"

    extra["runner"] = "both"
    assert account_runs_here(replace(account, extra=extra), "local") is True
    extra["runner"] = "off"
    assert account_runs_here(replace(account, extra=extra), "docker") is False


def _library_record(slug: str, topic: str, image: str = "") -> bytes:
    payload = {
        "topic": topic,
        "subject_slug": slug,
        "variant_index": 0,
        "humanized_caption": f"{topic} still does not fit the textbook story.",
        "visual_hook": topic.upper(),
    }
    if image:
        payload["image_relative"] = image
    return json.dumps(payload).encode()


def _prepare_channel(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OUTPUT_PATH", str(tmp_path))
    monkeypatch.setenv("PUBLISHER_MODE", "docker")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(
        "agents.posting.pinterest_engine.inventory.MasterInventory._load_ai_client",
        lambda self: (None, None),
    )


def test_dry_run_renders_library_photo_and_links_the_article(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from agents.posting.pinterest_engine.articles import Article

    _prepare_channel(monkeypatch, tmp_path)
    image_key = "ancient_knowledge/library/images/baalbek.jpg"
    record_key = "ancient_knowledge/library/post_site_link.json"
    library_row = _library_record(
        "baalbek-trilithon",
        "Baalbek trilithon",
        "images/baalbek.jpg",
    )
    client = FakeS3({record_key: library_row, image_key: _photo()})
    catalog = [
        Article(
            url=f"{_SITE}/baalbek",
            topic="Baalbek",
            body="The trilithon.",
            slug="baalbek",
        )
    ]
    stats = publish_account(
        _ancient(),
        pins=1,
        dry_run=True,
        client=client,
        catalog=catalog,
    )
    assert stats["published"] == 1
    assert client.downloads == [image_key]
    assert client.deletes == []
    cards = list((tmp_path / "pinterest" / "dryrun").glob("*.jpg"))
    assert len(cards) == 1
    card = Image.open(cards[0])
    assert card.size == (1000, 1500)
    inventory = json.loads(
        (tmp_path / "ancient_knowledge" / "master_inventory.json").read_text(encoding="utf-8")
    )
    assert inventory["entries"][0]["target_url"] == f"{_SITE}/baalbek"
    assert "Practical Guide" not in inventory["entries"][0]["pinterest_title"]
    assert not (tmp_path / "ancient_knowledge" / "_tmp").exists() or not any(
        (tmp_path / "ancient_knowledge" / "_tmp").rglob("*.jpg")
    )


def test_ledger_and_board_titles_block_a_repeat(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _prepare_channel(monkeypatch, tmp_path)
    record_key = "ancient_knowledge/library/post_tartaria.json"
    image_key = "ancient_knowledge/library/images/tartaria.jpg"
    ledger_key = "pinterest/ledger/ancient_knowledge.jsonl"
    row = json.dumps(
        {"account": "ancient_knowledge", "title": "Tartaria", "post_id": "", "pin_id": "old"}
    )
    client = FakeS3(
        {
            record_key: _library_record("tartaria", "Tartaria", "images/tartaria.jpg"),
            image_key: _photo(),
            ledger_key: (row + "\n").encode(),
        }
    )
    stats = publish_account(_ancient(), pins=1, dry_run=True, client=client, catalog=[])
    assert stats["published"] == 0
    assert stats["skipped"] >= 1
    assert image_key not in client.downloads

    fresh = FakeS3(
        {
            record_key: _library_record("tartaria", "Tartaria", "images/tartaria.jpg"),
            image_key: _photo(),
        }
    )
    seeded = publish_account(
        _ancient(),
        pins=1,
        dry_run=True,
        client=fresh,
        catalog=[],
        board_pins=[{"id": "board-pin", "title": "Tartaria"}],
    )
    assert seeded["published"] == 0
    assert b"Tartaria" in fresh.objects["pinterest/ledger/ancient_knowledge.jsonl"]


def test_flux_fallback_replaces_a_missing_or_title_graphic(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _prepare_channel(monkeypatch, tmp_path)
    record_key = "ancient_knowledge/library/post_puma.json"
    graphic_key = "ancient_knowledge/library/images/puma-title.jpg"
    client = FakeS3(
        {
            record_key: _library_record("puma-punku", "Puma Punku", "images/puma-title.jpg"),
            graphic_key: _title_graphic(),
        }
    )
    calls: list[str] = []

    def fake_flux(*, topic, channel_id, dest_dir, stem):
        calls.append(topic)
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / f"{stem}.jpg"
        path.write_bytes(_photo())
        return path

    monkeypatch.setattr(
        "agents.posting.pinterest_engine.flux_photo.generate_flux_photo",
        fake_flux,
    )
    stats = publish_account(_ancient(), pins=1, dry_run=True, client=client, catalog=[])
    assert stats["published"] == 1
    assert calls
    uploaded = [key for key in client.objects if key.endswith("puma-punku.jpg")]
    assert uploaded
    assert client.deletes == []


def test_flux_asks_for_a_portrait_frame(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from agents.posting.pinterest_engine.flux_photo import (
        FLUX_PIN_HEIGHT,
        FLUX_PIN_WIDTH,
        generate_flux_photo,
    )

    seen: dict = {}

    class _Adapter:
        def generate(self, _prompt, **kwargs):
            seen.update(kwargs)
            dest = Path(kwargs["output_directory"]) / f"{kwargs['output_stem']}.jpg"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(_photo())
            return dest

    monkeypatch.setattr(
        "agents.media.providers.image_provider.get_image_adapter",
        lambda **_kwargs: _Adapter(),
    )
    path = generate_flux_photo(
        topic="Baalbek",
        channel_id="ancient_knowledge",
        dest_dir=tmp_path,
        stem="baalbek",
    )
    assert path is not None and path.is_file()
    assert seen["width"] == FLUX_PIN_WIDTH == 832
    assert seen["height"] == FLUX_PIN_HEIGHT == 1248
    assert seen["aspect_ratio"] == "2:3"


def test_publish_sends_alt_text_and_does_not_patch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agents.posting.pinterest_engine.publisher import PinterestPublisher

    captured: dict = {}

    def _capture(self, payload):
        captured.update(payload)
        return {"id": "pin_new"}

    monkeypatch.setattr(PinterestPublisher, "_post_with_retry", _capture)
    publisher = PinterestPublisher(
        access_token="tok",
        board_id="board-1",
        require_board=False,
    )
    result = publisher.publish(
        {
            "post_id": "post_baalbek",
            "topic": "Baalbek trilithon",
            "pinterest_title": "Baalbek Trilithon",
            "pinterest_caption": "The quarry stones are still there.",
            "target_url": f"{_SITE}/baalbek",
            "publication_status": {"posted_on_pinterest": False},
        },
        b"jpeg-bytes",
    )
    assert result["id"] == "pin_new"
    assert captured["alt_text"] == "Baalbek Trilithon"
    assert captured["link"] == f"{_SITE}/baalbek"
    assert captured["description"].endswith(f"{_SITE}/baalbek")
    assert len(captured["description"]) <= 220
