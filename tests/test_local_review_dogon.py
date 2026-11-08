# -*- coding: utf-8 -*-
"""The Dogon local-review command keeps distribution off and leaves the original."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest
from PIL import Image

from production.pipeline.cli_parser import parse_cli_args
from production.pipeline.local_review import (
    DOGON_MP4_NAME,
    DOGON_STEM,
    DOGON_SUBJECT,
    active,
    begin,
    distribution_enabled,
    dogon_argv,
    end,
    isolate_dir,
    refuse_output,
    render_argv_from_qa,
    sample_dest_for,
    sample_video,
    topic_from_qa,
    validate_args,
    youtube_publish_enabled,
)


def _args(**overrides: object) -> argparse.Namespace:
    base = dict(
        local_review=True,
        topic=DOGON_SUBJECT,
        expect_stem=DOGON_STEM,
        post_type="ECONOMIC_REEL",
        page="ancient_knowledge",
        quantity=1,
        publish_youtube=False,
        schedule_uploads=False,
        resume_youtube_queue=False,
        protect_path=None,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def test_parser_accepts_local_review_without_a_publish_flag() -> None:
    args = parse_cli_args([
        DOGON_STEM,
        "--channel", "ancient_knowledge",
        "--post-type", "ECONOMIC_REEL",
        "--quantity", "1",
        "--local-review",
        "--expect-stem", DOGON_STEM,
    ])
    assert args.local_review is True
    assert args.publish_youtube is False
    help_text = Path("production/pipeline/cli_parser.py").read_text(encoding="utf-8")
    assert "ENABLE_YOUTUBE_UPLOAD" in help_text
    assert "not --dry-run" in help_text


def test_dogon_argv_has_no_distribution_flags() -> None:
    argv = dogon_argv(DOGON_STEM, "G:/ancient/clips/" + DOGON_MP4_NAME)
    assert argv[0] == DOGON_SUBJECT
    assert "4ed43c" not in argv[0]
    assert "--local-review" in argv
    assert "--quantity" in argv and argv[argv.index("--quantity") + 1] == "1"
    assert "--channel" in argv and argv[argv.index("--channel") + 1] == "ancient_knowledge"
    assert "--post-type" in argv and argv[argv.index("--post-type") + 1] == "ECONOMIC_REEL"
    assert "--expect-stem" in argv and argv[argv.index("--expect-stem") + 1] == DOGON_STEM
    assert "--publish-youtube" not in argv
    assert "--upload-youtube" not in argv
    assert "--dry-run" not in argv
    assert "--resume-youtube-queue" not in argv
    with pytest.raises(SystemExit):
        dogon_argv("A different monument", DOGON_MP4_NAME)


def test_qa_json_builds_the_protected_invocation(tmp_path: Path) -> None:
    video = tmp_path / DOGON_MP4_NAME
    video.write_bytes(b"original-bytes")
    qa = tmp_path / "the_sirius_mystery_dogon_4ed43c_v01_v01.qa.json"
    qa.write_text(
        json.dumps({"video_path": str(video), "verdict": "pass", "topic": ""}),
        encoding="utf-8",
    )
    argv = render_argv_from_qa(qa)
    parsed = parse_cli_args(argv)
    assert parsed.local_review is True
    assert parsed.quantity == 1
    assert parsed.publish_youtube is False
    assert parsed.topic == DOGON_SUBJECT
    assert "4ed43c" not in parsed.topic
    assert Path(parsed.protect_path[0]).name == DOGON_MP4_NAME
    assert topic_from_qa({"topic": ""}) == DOGON_SUBJECT
    assert topic_from_qa({"topic": DOGON_STEM}) == DOGON_SUBJECT
    with pytest.raises(SystemExit):
        topic_from_qa({"topic": "The pyramids at Giza"})


def test_local_review_refuses_youtube_even_if_the_env_flag_is_on(tmp_path: Path) -> None:
    assert youtube_publish_enabled(False, True) is True
    page = tmp_path / "ancient_knowledge"
    original = page / "clips" / DOGON_MP4_NAME
    original.parent.mkdir(parents=True)
    original.write_bytes(b"keep")
    begin(page, expect_stem=DOGON_STEM, protected=[original])
    try:
        assert active()
        assert distribution_enabled() is False
        assert youtube_publish_enabled(True, True) is False
        legacy = page / "assets" / "ep_old"
        from production.pipeline.local_review import current

        state = current()
        assert state is not None
        assert isolate_dir(legacy, "assets/ep_new") == state.root / "assets" / "ep_new"
        with pytest.raises(SystemExit):
            refuse_output(original)
        fresh = current_root(page) / "reel_new_v01.mp4"
        refuse_output(fresh)
        fresh.write_bytes(b"new")
        with pytest.raises(SystemExit):
            refuse_output(fresh)
        assert original.read_bytes() == b"keep"
    finally:
        end()
    assert distribution_enabled() is True


def current_root(page: Path) -> Path:
    from production.pipeline.local_review import current

    state = current()
    assert state is not None
    return state.root


def test_validate_args_rejects_quantity_and_youtube() -> None:
    validate_args(_args())
    with pytest.raises(SystemExit):
        validate_args(_args(quantity=2))
    with pytest.raises(SystemExit):
        validate_args(_args(publish_youtube=True))
    with pytest.raises(SystemExit):
        validate_args(_args(resume_youtube_queue=True))


def test_sample_writes_a_manifest_and_not_an_approval(tmp_path: Path) -> None:
    video = tmp_path / DOGON_MP4_NAME
    video.write_bytes(b"video-bytes-unchanged")
    picture = tmp_path / "frame.jpg"
    Image.new("RGB", (96, 160), (86, 108, 72)).save(picture, format="JPEG")

    def grab(at: float, dest: Path) -> Path:
        del at
        dest.write_bytes(picture.read_bytes())
        return dest

    dest = sample_dest_for(video)
    manifest = sample_video(video, dest, duration_s=80.0, grab=grab)
    assert dest.is_file()
    assert video.read_bytes() == b"video-bytes-unchanged"
    assert not (video.with_name(video.stem + ".approval.json")).exists()
    assert manifest["approval_written"] is False
    assert manifest["distribution"] == "off"
    assert manifest["certifies_truth"] is False
    assert any(row["fault"] == "flat" for row in manifest["frames"])
    assert any(10.0 < row["at_s"] < 70.0 for row in manifest["frames"])
    with pytest.raises(SystemExit):
        sample_video(video, dest, duration_s=80.0, grab=grab)


def test_powershell_entry_calls_the_module_and_names_the_qa_switch() -> None:
    script = Path(
        "channels_config/ancient_knowledge/tools/Invoke-DogonLocalReview.ps1"
    ).read_text(encoding="utf-8")
    assert "production.pipeline.local_review render --qa" in script
    assert "production.pipeline.local_review sample --video" in script
    assert "--publish-youtube" not in script
    assert "$env:ENABLE_YOUTUBE_UPLOAD" not in script
    assert "Mandatory = $true" in script
    assert "$QaJson" in script
