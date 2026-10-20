"""Shared CLI logging setup keeps the old formats and quiet loggers."""
from __future__ import annotations

import logging

from shared import logging as shared_logging
from utils import gemini_usage, ocr_engine, ocr_paraphrase


def test_setup_cli_logging(monkeypatch) -> None:
    calls: dict = {}
    urllib3 = logging.getLogger("urllib3")
    moviepy = logging.getLogger("moviepy")
    old_urllib3, old_moviepy = urllib3.level, moviepy.level
    monkeypatch.setattr(logging, "basicConfig", lambda **kw: calls.update(kw))
    try:
        shared_logging.setup_cli_logging(False, extra_noisy=("moviepy",))
        assert calls["format"] == "%(asctime)s  %(levelname)-8s  %(message)s"
        assert calls["datefmt"] == "%H:%M:%S" and calls["level"] == logging.INFO
        assert urllib3.level == logging.WARNING
        assert moviepy.level == logging.WARNING
        shared_logging.setup_cli_logging(True)
        assert calls["level"] == logging.DEBUG
    finally:
        urllib3.setLevel(old_urllib3)
        moviepy.setLevel(old_moviepy)


def test_ocr_and_hush_aliases() -> None:
    assert ocr_engine._configure_logging is shared_logging.setup_gemini_tool_logging
    assert ocr_paraphrase._configure_logging is shared_logging.setup_gemini_tool_logging
    assert gemini_usage.hush_gemini_sdk_logs is shared_logging.hush_gemini_sdk_logs
