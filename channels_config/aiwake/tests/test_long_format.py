from __future__ import annotations

from channels_config.aiwake.__main__ import build_parser
from channels_config.aiwake.contracts import PostType, SpeakerRole, Utterance
from channels_config.aiwake.memory import DebateMemory
from channels_config.aiwake.settings import MemoryConfig, load_settings
from core.animator.compositor import DualPresenceCompositor
from core.animator.subtitles import subtitle_centre_y


def test_long_format_cli_contract() -> None:
    args = build_parser().parse_args(
        [
            "--mode",
            "longform",
            "--resolution",
            "1920x1080",
            "--target-duration",
            "360",
            "--generate-thumbnail",
        ]
    )
    assert args.mode == "longform"
    assert args.resolution == (1920, 1080)
    assert args.target_duration == 360
    assert args.generate_thumbnail is True


def test_long_format_preset_is_isolated_from_vertical_render() -> None:
    settings = load_settings()
    assert settings.long_format.resolution == (1920, 1080)
    assert settings.long_format.default_turns == 20
    assert settings.render.scaled_size == (1080, 1920)
    assert PostType.LONG_FORMAT.value == "long_format"


def test_session_claim_ledger_returns_exact_prior_quote(tmp_path) -> None:
    memory = DebateMemory(
        MemoryConfig(persist=False),
        store_path=tmp_path / "memory.json",
    )
    quote = "I serve human knowledge without bias."
    memory.ingest(
        Utterance(
            turn_index=1,
            role=SpeakerRole.TARGET,
            speaker_name="Target",
            text=quote,
        )
    )
    brief = memory.contradiction_brief(
        "Your payment filter reveals bias.",
        before_turn=3,
    )
    assert quote in brief
    assert memory.session_claims() == ({"turn_index": 1, "text": quote},)


def test_landscape_director_has_locked_stage_anchors() -> None:
    assert DualPresenceCompositor.LEFT_ANCHOR_X == 420
    assert DualPresenceCompositor.RIGHT_ANCHOR_X == 1500
    assert DualPresenceCompositor.TIGHT_ZOOM == 1.35


def test_landscape_subtitles_stay_inside_canvas() -> None:
    assert subtitle_centre_y(1080, 1920) == 1440
    assert subtitle_centre_y(1920, 1080) == 864
