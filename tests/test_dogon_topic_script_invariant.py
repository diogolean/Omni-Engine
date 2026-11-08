# -*- coding: utf-8 -*-
"""The filename stem is not the episode, and a passed still is not another line.

The f6fcf15 local review spoke ``the_sirius_mystery_dogon_4ed43c`` and
attached an accepted depiction of a different sentence. These checks lock
that mapping before any further image retry.
"""
from __future__ import annotations

import json
from pathlib import Path

from production.formats.economic_reel.artefact.script_grounding import (
    grounding_problem,
    repair_hook,
)
from production.formats.economic_reel.artefact.script_length import soft_word_ceiling, AK_NARRATION_S
from production.formats.economic_reel.grounding.brief import build_episode_brief
from production.formats.economic_reel.grounding.memory import recall, remember
from production.formats.economic_reel.grounding.prompt import ground_image_prompt
from production.formats.economic_reel.grounding.still_cache import write_spoken_sidecar
from production.media.text_utils import is_technical_stem
from production.pipeline.local_review import (
    DOGON_NARRATION,
    DOGON_STEM,
    DOGON_SUBJECT,
    beat_evidence,
    begin,
    end,
    reviewed_narration,
)

_OTHER_LINE = "The Dogon people maintain their knowledge predates any European contact."
_THIS_LINE = (
    "The Sirius Mystery is Robert Temple's 1976 book about published accounts "
    "of Dogon star lore."
)


def test_the_bar_card_is_flat_and_a_textured_frame_is_not(tmp_path: Path) -> None:
    from PIL import Image

    from production.formats.economic_reel.artefact.reuse import _plain_fallback
    from production.qa.mp4_gate import frame_fault

    for index in (2, 3, 5, 9, 10, 11, 13):
        path = _plain_fallback(tmp_path / f"beat{index:02d}.jpg", index)
        assert frame_fault(path) == "flat"
    photo = tmp_path / "land.jpg"
    image = Image.new("RGB", (96, 160))
    pixels = image.load()
    for y in range(160):
        for x in range(96):
            pixels[x, y] = ((x * 7 + y) % 256, (y * 5) % 200, (x * 3) % 180)
    image.save(photo, format="JPEG", quality=90)
    assert frame_fault(photo) == ""


def test_a_fallback_card_does_not_replace_the_judged_image(tmp_path: Path) -> None:
    from production.qa.models import StillGateResult, preserve_judged_still

    judged = tmp_path / "attempt_03.png"
    judged.write_bytes(b"png")
    card = tmp_path / "act03_fallback.jpg"
    card.write_bytes(b"jpg")
    result = StillGateResult(
        path=judged,
        extra_generations=2,
        publishable=False,
        blocked_reason="documentary_fail:anachronism",
        attempts=[
            {
                "attempt": 1,
                "image_name": "attempt_01.png",
                "prompt": "SPOKEN BEAT: the sentence under test.",
                "rejection_reason": "anachronism",
                "relevance_score": 0.1,
                "qa_status": "scored",
            },
            {
                "attempt": 3,
                "image_name": "attempt_03.png",
                "prompt": "SPOKEN BEAT: the sentence under test.",
                "rejection_reason": "anachronism",
                "relevance_score": 0.0,
                "qa_status": "scored",
            },
        ],
    )
    row = result.beat_row(index=2, text="They encountered a people with detailed knowledge of invisible stars.")
    preserve_judged_still(row, card)
    assert row["image_path"].endswith("attempt_03.png")
    assert row["judged_image_path"].endswith("attempt_03.png")
    assert row["substitute_name"] == "act03_fallback.jpg"
    assert [item["image_name"] for item in row["attempt_log"]] == [
        "attempt_01.png",
        "attempt_03.png",
    ]


def test_a_claim_is_an_illustrative_place_not_a_person() -> None:
    from production.formats.economic_reel.grounding.prompt import ground_image_prompt

    script = (
        "French anthropologists arrived in the Bandiagara Escarpment of Mali. "
        "Historians debate whether oral traditions preserved data from millennia past."
    )
    prompt = ground_image_prompt(
        topic=DOGON_SUBJECT,
        script=script,
        spoken="Historians debate whether oral traditions preserved data from millennia past.",
        channel="ancient_knowledge",
    )
    low = prompt.lower()
    assert "cliff of bandiagara" in low
    assert "mali" in low
    assert "sandstone" in low
    assert "person" not in low
    assert "hoodie" not in low
    assert "show the land" not in low
    assert "illustrative" not in low
    assert "place already named" not in low
    assert "4ed43c" not in low


def test_the_sent_flux_prompt_keeps_mali_and_drops_the_drawn_words() -> None:
    from production.pipeline.local_review import still_payload
    from production.providers.image.together_image import sanitize_prompt_for_flux

    payload = still_payload()
    sent = sanitize_prompt_for_flux(payload["prompt_sent"], visual_role="documentary")
    low = sent.lower()
    assert payload["guidance_scale"] == 0.0
    assert payload["negative_conditions_this_model"] is False
    assert "cliff of bandiagara" in low
    assert "mali" in low
    assert "sandstone" in low
    for banned in ("person", "hoodie", "illustrative", "do not", "show the land", "place already named"):
        assert banned not in low


def test_the_stem_is_not_a_spoken_subject() -> None:
    assert is_technical_stem(DOGON_STEM)
    brief = build_episode_brief(DOGON_STEM, "")
    assert "4ed43c" not in brief.hero_short
    assert "the_" not in brief.hero_short
    repaired = repair_hook("The cliff villages stand above the plain.", DOGON_STEM)
    assert "4ed43c" not in repaired
    assert not repaired.startswith(DOGON_STEM)


def test_reviewed_narration_does_not_invent_records_or_speak_the_stem() -> None:
    text = DOGON_NARRATION
    low = text.lower()
    assert "4ed43c" not in low
    assert "the_sirius_mystery" not in low
    assert "ancient records" not in low
    assert "nineteenth century" not in low
    assert "align" not in low
    assert "griaule" in low
    assert "1976" in text
    assert "later researchers" in low
    assert "published accounts" in low
    assert "sixty years" in low
    assert "not, by itself" in low
    assert "does not establish" in low
    assert "historians" not in low
    assert "wrote down" not in low
    assert len(text.split()) <= soft_word_ceiling(AK_NARRATION_S)
    assert grounding_problem(text, DOGON_SUBJECT) == ""


def test_an_accepted_depiction_does_not_cross_spoken_lines(tmp_path: Path) -> None:
    remember(
        channel="ancient_knowledge",
        topic=DOGON_SUBJECT,
        spoken=_OTHER_LINE,
        visual_subject="a cliff village the earlier line accepted",
        passed=True,
        score=0.9,
        root=tmp_path,
    )
    hints = recall(
        channel="ancient_knowledge",
        topic=DOGON_SUBJECT,
        spoken=_THIS_LINE,
        root=tmp_path,
    )
    assert hints.accepted == []
    prompt = ground_image_prompt(
        topic=DOGON_SUBJECT,
        script=DOGON_NARRATION,
        spoken=_THIS_LINE,
        channel="ancient_knowledge",
        memory_root=tmp_path,
    )
    assert "PREVIOUSLY ACCEPTED DEPICTION" not in prompt
    assert "4ed43c" not in prompt
    same = recall(
        channel="ancient_knowledge",
        topic=DOGON_SUBJECT,
        spoken=_OTHER_LINE,
        root=tmp_path,
    )
    assert same.accepted == ["a cliff village the earlier line accepted"]


def test_local_review_speaks_the_checked_script_not_the_stem(tmp_path: Path) -> None:
    page = tmp_path / "page"
    page.mkdir()
    begin(page, expect_stem=DOGON_STEM, protected=[])
    try:
        spoken = reviewed_narration()
    finally:
        end()
    assert spoken == DOGON_NARRATION
    assert reviewed_narration() == ""


def test_evidence_pairs_a_beat_with_its_sidecar_and_not_another_sentence(tmp_path: Path) -> None:
    image = tmp_path / "act01.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 64)
    write_spoken_sidecar(image, _OTHER_LINE, passed=True, score=0.8)
    qa = {
        "topic": DOGON_STEM,
        "video_path": "",
        "beats": [
            {
                "index": 0,
                "text": _THIS_LINE,
                "prompt": "EPISODE OBJECT: the the_sirius_mystery_dogon_4ed43c.",
                "image_path": str(image),
                "passed": False,
                "reason": "anachronism,setting_contradicts_beat",
                "score": 0.2,
            }
        ],
    }
    rows = beat_evidence(qa, tmp_path)
    assert rows[0]["image_name"] == "act01.png"
    assert rows[0]["spoken"] == _THIS_LINE
    assert rows[0]["sidecar_spoken"] == _OTHER_LINE
    assert rows[0]["sidecar_matches_spoken"] is False
    assert "4ed43c" in rows[0]["prompt"]
    assert str(tmp_path) not in json.dumps(rows)
