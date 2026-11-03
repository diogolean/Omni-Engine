# -*- coding: utf-8 -*-
"""Offline smoke for the photography lock and YouTube tags.

No network, no credentials, and no upload. The style lock follows
PHOTOGRAPHY_STYLE_LOCK. Tags come from each page's YOUTUBE_DEFAULT_TAGS.
"""
from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_ARCHITECT = _ROOT / "production" / "formats" / "mei" / "visual_architect.py"
_CHANNEL_IDS = (
    "anna_protocol",
    "master_mei",
    "wonder_feed",
    "down_dirty",
    "ancient_knowledge",
    "momma_circle",
    "principles_of_wealth_finance_economics",
    "endless_summer_paradise",
    "aiwake",
)
_METAPHOR = "The electrical grid of adrenal ignition"


def test_photography_lock_follows_the_page_flag(monkeypatch) -> None:
    from production.formats.mei import visual_architect as architect
    from shared.channel_flags import flag

    assert flag("anna_protocol", "PHOTOGRAPHY_STYLE_LOCK") is True
    assert flag("wonder_feed", "PHOTOGRAPHY_STYLE_LOCK") is False
    assert flag("ancient_knowledge", "PHOTOGRAPHY_STYLE_LOCK") is False
    assert flag("", "PHOTOGRAPHY_STYLE_LOCK") is False

    grounded = architect.ground_image_concept(_METAPHOR, page_id="anna_protocol").lower()
    assert "electrical" not in grounded
    assert "ignition" not in grounded
    assert "herb" in grounded

    untouched = architect.ground_image_concept(_METAPHOR, page_id="wonder_feed").lower()
    assert "electrical grid" in untouched
    assert "adrenal ignition" in untouched
    assert architect.ground_image_concept(_METAPHOR, page_id="") == architect.ground_image_concept(
        _METAPHOR, page_id="wonder_feed"
    )

    slide = architect.build_carousel_slide_prompt(
        topic="Celtic sea salt",
        visual_subject="glowing veins and a hologram of the nervous system",
        facet="Macro of salt crystals on a wooden board.",
        slide_num=1,
        total=3,
        page_id="anna_protocol",
    ).lower()
    assert "glowing veins" not in slide
    assert "a hologram of" not in slide
    assert "leaf veins" in slide or "amber glass" in slide
    assert "sage green" in slide or "terracotta" in slide
    assert "no holograms" in slide
    assert "no sci-fi" in slide

    class _Channel:
        def __init__(self, channel_id: str) -> None:
            self.channel_id = channel_id

        def compose_image_prompt(self, topic: str) -> str:
            return topic

    locked = architect.VisualArchitect(channel=_Channel("anna_protocol")).build_prompt(
        "Celtic sea salt mineral replenishment",
        avatar_mode="OFF",
    ).lower()
    assert "kitchen apothecary" in locked
    assert "no sci-fi elements" in locked

    open_page = architect.VisualArchitect(channel=_Channel("down_dirty")).build_prompt(
        "rain on asphalt",
        avatar_mode="OFF",
    ).lower()
    assert "kitchen apothecary" not in open_page
    assert "no sci-fi elements" not in open_page

    def _only_down_dirty(page_id: str, name: str, default: bool = False) -> bool:
        return page_id == "down_dirty" and name == "PHOTOGRAPHY_STYLE_LOCK"

    monkeypatch.setattr(architect, "flag", _only_down_dirty)
    moved = architect.ground_image_concept("electrical grid", page_id="down_dirty").lower()
    assert "herb" in moved
    named = architect.ground_image_concept("electrical grid", page_id="anna_protocol").lower()
    assert "electrical" in named

    source = _ARCHITECT.read_text(encoding="utf-8")
    for channel_id in _CHANNEL_IDS:
        assert channel_id not in source


def test_youtube_tags_come_from_page_config() -> None:
    from agents.posting.publish_existing import build_tags
    from distribution.publishers.api.youtube_upload import _default_tags_for_page
    from shared.channel_flags import discover_pages, value

    anna = build_tags([], "anna_protocol")
    configured = [str(tag) for tag in value("anna_protocol", "YOUTUBE_DEFAULT_TAGS")]
    assert anna == configured
    assert _default_tags_for_page("anna_protocol") == configured
    lowered = {tag.lower() for tag in anna}
    assert "relationship advice" not in lowered
    assert "self-growth" not in lowered
    blob = " ".join(anna).lower()
    assert "natural remedies" in blob
    assert "ancestral wellness" in blob
    assert "shorts" in lowered

    publisher = (_ROOT / "agents" / "posting" / "publish_existing.py").read_text(encoding="utf-8")
    assert "relationship advice" not in publisher
    assert "YOUTUBE_DEFAULT_TAGS" in publisher

    for page in discover_pages():
        expected = _default_tags_for_page(page)
        assert build_tags([], page) == expected
        page_tags = value(page, "YOUTUBE_DEFAULT_TAGS", None)
        if isinstance(page_tags, (list, tuple)) and page_tags:
            assert expected == [str(tag) for tag in page_tags]
        else:
            assert expected == ["shorts"]

    merged = build_tags(["castor oil"], "Anna_Protocol", extra=["herbs"])
    assert merged[0] == "anna protocol"
    assert "herbs" in merged
    assert "castor oil" in merged
    assert len(merged) <= 30
