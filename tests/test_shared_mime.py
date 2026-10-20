"""One image MIME helper for the vision payloads."""
from __future__ import annotations

from agents.media import style_reader, vision_agent
from shared.mime import image_mime_for


def test_mime() -> None:
    assert image_mime_for("A.PNG") == "image/png"
    assert image_mime_for("b.webp") == "image/webp"
    assert image_mime_for("c.jpeg") == "image/jpeg"
    assert image_mime_for("d.gif") == "image/jpeg"


def test_aliases() -> None:
    assert style_reader._mime_for is image_mime_for
    assert vision_agent._mime_for is image_mime_for
