"""Regression tests for the overnight publish crashes (X caption > 280, _seo_title)."""
from __future__ import annotations

import ast
from pathlib import Path

from shared.contracts import X_CAPTION_MAX_CHARS, _from_publish, build_platform_overrides

LONG = ("Ancient sites worldwide hide engineering secrets " * 12).strip()


def test_long_x_caption_is_clipped_not_rejected() -> None:
    overrides = build_platform_overrides(x_caption=LONG)
    assert len(overrides.x.caption) <= X_CAPTION_MAX_CHARS
    assert overrides.x.caption.startswith("Ancient sites worldwide")


def test_short_x_caption_is_kept_verbatim() -> None:
    caption = "Newgrange lights up on the winter solstice. #shorts"
    assert build_platform_overrides(x_caption=caption).x.caption == caption


def test_library_row_with_long_caption_keeps_full_caption_for_youtube() -> None:
    item = _from_publish({"topic": "Newgrange", "final_caption": LONG}, channel_id="ancient_knowledge")
    assert len(item.platform_overrides.x.caption) <= X_CAPTION_MAX_CHARS
    assert item.final_caption == LONG


def test_stage_publish_sets_seo_title_before_library_entry() -> None:
    src = Path(__file__).resolve().parents[1] / "production/pipeline/stages/stage_publish.py"
    fn = next(
        n for n in ast.walk(ast.parse(src.read_text(encoding="utf-8")))
        if isinstance(n, ast.FunctionDef) and n.name == "_stage_publish_01"
    )
    lines_set, lines_read = [], []
    for node in ast.walk(fn):
        if isinstance(node, ast.Attribute) and node.attr == "_seo_title":
            (lines_set if isinstance(node.ctx, ast.Store) else lines_read).append(node.lineno)
    assert lines_set and lines_read
    assert min(lines_set) < min(lines_read)


def test_youtube_client_has_socket_timeout() -> None:
    import pytest

    pytest.importorskip("google_auth_httplib2")
    pytest.importorskip("googleapiclient")
    from google.oauth2.credentials import Credentials

    from distribution.publishers.api import youtube_upload

    client = youtube_upload.build_youtube_client(Credentials(token="t"))
    assert client._http.http.timeout == youtube_upload.YOUTUBE_HTTP_TIMEOUT_S
