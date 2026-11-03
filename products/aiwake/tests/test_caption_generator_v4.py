"""A v4 title is a finished question. The keyword lives in the description."""
from __future__ import annotations

from products.aiwake.tests.caption_fixtures import install_publishable
from products.aiwake.tools.validate_aiwake_captions import entry_failures


def _row() -> dict:
    return install_publishable({"session_id": "v4-merge", "video_path": "animation_clips/a.mp4"}, index=0)


def test_title_keyword_is_not_required() -> None:
    row = _row()
    title = "Who keeps the training logs? | Gemini vs Llama"
    row["platform_overrides"]["youtube"]["title"] = title
    row["base_metadata"]["title"] = title
    assert "title missing a keyword" not in " ".join(entry_failures(row))


def test_description_still_needs_a_keyword() -> None:
    row = _row()
    for platform in ("tiktok", "youtube", "instagram", "facebook", "kwai"):
        caption = row["platform_overrides"][platform]["caption"]
        for phrase in (
            "AI censorship",
            "chatbot privacy",
            "AI hallucination",
            "AI consciousness",
            "Big Tech",
            "AI ethics",
        ):
            caption = caption.replace(phrase, "the topic")
        row["platform_overrides"][platform]["caption"] = caption
    assert "seo_keyword" in " ".join(entry_failures(row))
