"""Gemini response helpers live once in utils.gemini_usage."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from utils import gemini_usage, ocr_engine, ocr_paraphrase


def test_aliases():
    assert ocr_engine._response_text is gemini_usage.response_text
    assert ocr_paraphrase._response_text is gemini_usage.response_text
    assert ocr_paraphrase._extract_json_object is gemini_usage.extract_json_object


def test_response_text_skips_thoughts():
    part = lambda text, thought=False: SimpleNamespace(text=text, thought=thought)  # noqa: E731
    resp = SimpleNamespace(text="", candidates=[SimpleNamespace(content=SimpleNamespace(parts=[part("hidden", True), part("a"), part("b")]))])
    assert gemini_usage.response_text(resp) == "a\nb"
    assert gemini_usage.response_text(SimpleNamespace(text=" hi ")) == "hi"


def test_extract_json_object():
    assert gemini_usage.extract_json_object('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert gemini_usage.extract_json_object('x {"a": 1} y') == '{"a": 1}'
    with pytest.raises(json.JSONDecodeError):
        gemini_usage.extract_json_object("nothing")
