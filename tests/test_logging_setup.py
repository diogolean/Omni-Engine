"""Shared CLI logging setup keeps the old formats and quiet loggers."""
from __future__ import annotations

import logging

from utils import logging_setup, ocr_engine, ocr_paraphrase


def test_setup_cli_logging(monkeypatch):
    calls = {}
    monkeypatch.setattr(logging, "basicConfig", lambda **kw: calls.update(kw))
    logging.getLogger("moviepy").setLevel(logging.NOTSET)
    logging_setup.setup_cli_logging(False, extra_noisy=("moviepy",))
    assert calls["format"] == "%(asctime)s  %(levelname)-8s  %(message)s"
    assert calls["datefmt"] == "%H:%M:%S" and calls["level"] == logging.INFO
    assert logging.getLogger("urllib3").level == logging.WARNING
    assert logging.getLogger("moviepy").level == logging.WARNING
    logging_setup.setup_cli_logging(True)
    assert calls["level"] == logging.DEBUG


def test_ocr_aliases():
    assert ocr_engine._configure_logging is logging_setup.setup_gemini_tool_logging
    assert ocr_paraphrase._configure_logging is logging_setup.setup_gemini_tool_logging
