# -*- coding: utf-8 -*-
"""Provider chain, Flux generic scenes, and the shared still/frame judge."""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from PIL import Image

from agents.media.free_images.http import HttpResponse
from modules.reel_artefact.hook import wrap_reel_generate
from modules.reel_artefact.route import acquire_photo, generic_flux_prompt
from modules.reel_qa.frames import strip_subtitle_band
from modules.reel_qa.mp4_gate import SubtitleCue, beat_for_cue, gate_final_mp4
from modules.reel_qa.relevance import _verdict_from_payload
from modules.shot_planner.entities import Entity
from modules.shot_planner.render import render_shot
from modules.shot_planner.schema import Shot


def _jpeg(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (180, 140, 40)).save(buf, format="JPEG")
    return buf.getvalue()


def _page(title: str, width: int, height: int, license_name: str, url: str) -> dict:
    return {
        "title": title,
        "imageinfo": [
            {
                "url": url,
                "descriptionurl": "https://commons.wikimedia.org/wiki/" + title.replace(" ", "_"),
                "width": width,
                "height": height,
                "mime": "image/jpeg",
                "extmetadata": {
                    "ObjectName": {"value": title.replace("File:", "")},
                    "LicenseShortName": {"value": license_name},
                    "Artist": {"value": "Example Photographer"},
                    "ImageDescription": {"value": title},
                    "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/4.0/"},
                },
            }
        ],
    }


class _Client:
    def __init__(self, pages: dict, files: dict[str, bytes]) -> None:
        self.pages = pages
        self.files = files

    def get(self, url, params=None, headers=None):
        del headers
        params = params or {}
        if url in self.files:
            return HttpResponse(200, self.files[url])
        if params.get("titles"):
            payload = {"query": {"pages": self.pages["titles"]}}
            return HttpResponse(200, json.dumps(payload).encode())
        if params.get("gsrsearch"):
            payload = {"query": {"pages": self.pages["search"]}}
            return HttpResponse(200, json.dumps(payload).encode())
        return HttpResponse(404, b"")


def _entity() -> Entity:
    return Entity(
        id="sample",
        name="Sample artefact",
        aliases=("sample",),
        kind="artifact",
        canonical_visual="the object",
        era="ancient",
        setting="museum case",
        confusions=(),
        channels=("ancient_knowledge",),
        canonical_shape="a distinct shape",
        commons_search=("sample artefact",),
        commons_titles=("File:Small.jpg",),
    )


def test_chain_edits_the_reference_when_the_photo_is_too_small(tmp_path: Path) -> None:
    url = "https://example.test/small.jpg"
    client = _Client(
        pages={
            "titles": {"1": _page("File:Small.jpg", 400, 300, "Public domain", url)},
            "search": {},
        },
        files={url: _jpeg(400, 300)},
    )

    def _generate(prompt: str, dest: Path, reference) -> str:
        assert reference is not None and Path(reference).is_file()
        assert "distinct shape" in prompt
        Image.new("RGB", (1080, 1920), (30, 30, 30)).save(dest, format="JPEG")
        return "gemini-2.5-flash-image"

    dest = tmp_path / "out.jpg"
    path = acquire_photo(
        _entity(),
        dest,
        channel="ancient_knowledge",
        mode="chain",
        client=client,
        cache_dir=tmp_path / "cache",
        generate_image=_generate,
    )
    assert path == dest and dest.is_file()
    side = json.loads(Path(str(dest) + ".source.json").read_text(encoding="utf-8"))
    assert side["provider"] == "nanobanana"
    assert side["reference_image"]


def test_flux_beats_do_not_ask_for_the_artefact(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REEL_ARTIFACT_IMAGES", "1")
    shot = Shot(
        id="s1",
        narration="An archaeologist works at dusk beside the excavation.",
        entity_id="quimbaya_ornaments",
        subject="Quimbaya golden ornaments",
        canonical_visual="small gold pendants with delta wings",
        shot_type="wide",
        camera="wide documentary view, open sky",
        setting="excavation at dusk",
        era="ancient",
        mood="quiet",
        lighting="dusk",
        image_role="flux",
        canonical_shape="a small gold pendant with delta wings, a tail fin, and a stylised head",
    )
    rendered = render_shot(
        shot,
        config={"style_lock": "", "hard_negatives": [], "identity_negatives": []},
    )
    positive = rendered.positive
    assert "documentary scene" in positive.lower()
    assert "delta wings" not in positive.lower()
    assert "no close-up" in generic_flux_prompt("Setting: a museum hall.").lower()


def test_flag_off_does_not_wrap_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REEL_ARTIFACT_IMAGES", raising=False)
    monkeypatch.delenv("REEL_ARTIFACT_IMAGE_PROVIDER", raising=False)
    sentinel = object()
    wrapped, extra = wrap_reel_generate(sentinel, channel="ancient_knowledge", shot=None)
    assert wrapped is sentinel
    assert extra == {}


def test_frame_judge_uses_the_beat_prompt_and_drops_subtitles(tmp_path: Path) -> None:
    video = tmp_path / "reel.mp4"
    video.write_bytes(b"mp4")
    frame = tmp_path / "frame.jpg"
    Image.new("RGB", (90, 160), (255, 0, 0)).save(frame, format="JPEG")
    seen: dict = {}

    def _judge(path, text, prompt, **kwargs):
        seen["text"] = text
        seen["prompt"] = prompt
        seen["shape"] = kwargs.get("canonical_shape")
        with Image.open(path) as image:
            seen["size"] = image.size
        return {
            "is_relevant": True,
            "relevance_score": 0.95,
            "qa_status": "scored",
            "rejection_reason": None,
        }

    def _probe(path: Path) -> dict:
        del path
        return {
            "error": "",
            "duration_s": 22.0,
            "has_audio": True,
            "mean_volume_db": -16.0,
            "max_volume_db": -3.0,
        }

    def _grab(video_path: Path, at_s: float, dest: Path) -> Path:
        del video_path, at_s
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(frame.read_bytes())
        return dest

    cue = SubtitleCue(text="small gold pendant", start_s=0.5, end_s=1.5)
    decision = gate_final_mp4(
        video,
        [cue],
        probe_fn=_probe,
        evaluate_fn=_judge,
        frame_fn=_grab,
        work_dir=tmp_path / "frames",
        beats=[
            {
                "text": "A small gold pendant with delta wings sits in the case.",
                "prompt": "EXPECTED SUBJECT: Quimbaya golden ornaments.",
                "canonical_shape": "delta wings, tail fin, stylised head",
                "reference_image": "",
            }
        ],
    )
    assert decision["passed"] is True
    assert seen["prompt"].startswith("EXPECTED SUBJECT")
    assert "delta wings" in seen["text"]
    assert seen["shape"].startswith("delta wings")
    assert seen["size"][1] < 160
    assert beat_for_cue(cue, [{"text": "unrelated"}]) is None


def test_shape_mismatch_fails_the_still(tmp_path: Path) -> None:
    image = tmp_path / "still.jpg"
    Image.new("RGB", (32, 32), (0, 0, 0)).save(image, format="JPEG")
    verdict = _verdict_from_payload(
        {
            "image_description": "a museum photograph of a gold pendant",
            "is_relevant": True,
            "relevance_score": 0.95,
            "detected_subjects": ["pendant"],
            "shape_mismatch": True,
            "anachronism": False,
            "non_photoreal": False,
            "anatomy_defect": False,
            "setting_contradicts_beat": False,
        },
        image_path=image,
        chunk_text="The pendant.",
        banned_subjects=None,
        provider="test",
        model_id="test",
    )
    assert verdict["is_relevant"] is False
    assert "shape_mismatch" in verdict["rejection_reason"]


def test_subtitle_band_is_cropped_before_the_frame_judge(tmp_path: Path) -> None:
    src = tmp_path / "frame.jpg"
    Image.new("RGB", (100, 200), (1, 2, 3)).save(src, format="JPEG")
    clean = strip_subtitle_band(src, tmp_path / "clean.jpg")
    assert clean is not None
    assert Image.open(clean).size[1] == int(200 * 0.76)
