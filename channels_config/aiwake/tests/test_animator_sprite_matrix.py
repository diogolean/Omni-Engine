from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image, ImageDraw

from channels_config.aiwake.animator_bridge import (
    END_PADDING_S,
    SPEECH_TAIL_LINGER_S,
    _fade_speech_edges,
    _resample,
    build_session_audio,
    debate_video_filename,
    resolve_character_map,
    resolve_dialectic_emotion,
)
from channels_config.aiwake.contracts import SpeakerRole
from channels_config.aiwake.media.audio import (
    DEEPSEEK_CANONICAL_VOICE,
    GEMINI_CANONICAL_VOICE,
    LLAMA_CANONICAL_VOICE,
    resolve_cta_voice,
    resolve_voice,
)
from channels_config.aiwake.settings import AudioConfig
from core.animator.audio_analyzer import (
    AudioAnalyzer,
    active_speaker_lookup,
    camera_tight_lookup,
    emotion_lookup,
    speaking_speaker_lookup,
)
from core.animator.compositor import (
    CAMERA_NORMAL,
    CAMERA_NORMAL_ZOOM,
    CAMERA_TIGHT,
    CAMERA_TIGHT_ZOOM,
    GEMINI_LEAD_X,
    LLAMA_LEAD_X,
    POST_ROLL_S,
    lead_anchor_x,
    ShotReverseShotCompositor,
    _HeroCamera,
    dramatic_camera_mode,
    emphasis_head_target,
)
from core.animator.puppet import (
    BROW_STATES,
    REQUIRED_REST_MOUTH_STATES,
    REST_MOUTH_STATES,
    PuppetRig,
    PuppetSkin,
    emotion_brow_angles,
    emotion_brow_state,
    emotion_rest_mouth_state,
    onset_rest_mouth,
    supported_rest_mouth,
)
from core.animator.renderer import AnimationRenderer
from core.animator.subtitles import build_ass
from core.animator.types import VISEMES, DialogueTurn, SpeakerStyle


def _layer(path: Path, size: tuple[int, int], box: tuple[int, int, int, int]) -> None:
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle(box, fill=(70, 80, 95, 255))
    image.save(path)


def test_manifestless_high_resolution_external_sprite_matrix_is_preserved(tmp_path: Path) -> None:
    puppet_dir = tmp_path / "external_cyborg"
    puppet_dir.mkdir()
    size = (960, 1520)
    _layer(puppet_dir / "body.png", size, (160, 650, 800, 1480))
    _layer(puppet_dir / "head.png", size, (280, 180, 680, 760))
    _layer(puppet_dir / "eyes_open.png", size, (360, 420, 600, 470))
    original_body = (puppet_dir / "body.png").read_bytes()

    skin = PuppetSkin.load_or_create(puppet_dir)
    manifest_path = puppet_dir / "puppet.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.setdefault("calibration", {})["eye_bboxes"] = [
        [350, 410, 470, 520],
        [500, 410, 620, 520],
    ]
    manifest["anchors"].update(
        {
            "left_eye": [410, 465],
            "right_eye": [560, 465],
            "eye_radius": 55,
        }
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    skin = PuppetSkin.load(puppet_dir)
    rig = PuppetRig(skin)
    camera = _HeroCamera(rig, None)
    native_camera = _HeroCamera(rig, None, target_width=size[0], target_height=size[1])

    assert rig.canvas_size == size
    assert camera.pixel_aspect_error < 0.001
    assert native_camera.crop == (0, 0, size[0], size[1])
    assert (native_camera.out_w, native_camera.out_h) == size
    assert camera._update_head_angle(  # noqa: SLF001 - verifies stateful easing contract
        t=0.1,
        rms=0.5,
        emphasis_threshold=0.75,
        is_speaking=True,
    ) == 0.0
    spike_angle = camera._update_head_angle(  # noqa: SLF001
        t=0.5,
        rms=1.0,
        emphasis_threshold=0.75,
        is_speaking=True,
    )
    assert 0.0 < abs(spike_angle) <= 1.2
    eased_angle = camera._update_head_angle(  # noqa: SLF001
        t=0.6,
        rms=0.4,
        emphasis_threshold=0.75,
        is_speaking=False,
    )
    assert 0.0 < abs(eased_angle) < abs(spike_angle)
    assert camera._update_emotion("inquisitor") == ("neutral", 1 / 3)  # noqa: SLF001
    assert camera._update_emotion("inquisitor") == ("neutral", 2 / 3)  # noqa: SLF001
    assert camera._update_emotion("inquisitor") == ("neutral", 1.0)  # noqa: SLF001
    brow_angles = [
        camera._update_brow_angle("inquisitor")  # noqa: SLF001
        for _ in range(4)
    ]
    assert 0.0 > brow_angles[0] > brow_angles[1] > brow_angles[2] > brow_angles[3]
    assert brow_angles[-1] == -5.5
    assert (puppet_dir / "eyes_half.png").is_file()
    assert (puppet_dir / "eyes_blink.png").is_file()
    assert (puppet_dir / "bg.png").is_file()
    assert all((puppet_dir / f"mouth_{viseme}.png").is_file() for viseme in VISEMES)
    assert all(
        (
            puppet_dir
            / (
                "mouth_X_neutral.png"
                if state == "neutral"
                else f"mouth_{state}.png"
            )
        ).is_file()
        for state in REQUIRED_REST_MOUTH_STATES
    )
    assert (puppet_dir / "body.png").read_bytes() == original_body
    assert skin.mouth_style == "ghibli_mecha"
    assert skin.anchors.left_eye == (410, 465)
    assert skin.anchors.right_eye == (560, 465)
    assert skin.anchors.eye_radius == 55
    assert set(skin.palette or {}) == {
        "ink_outline",
        "accent_color",
        "casing_color",
        "cavity_interior",
        "teeth_color",
    }

    frame = rig.compose(viseme="D", eye_state=0, y_offset=3.0)
    assert frame.shape == (size[1], size[0], 4)

    neutral = rig.compose(viseme="D", eye_state=0, head_angle=0.0)
    inquisitor = rig.compose(viseme="D", eye_state=0, emotion="inquisitor")
    tilted = rig.compose(viseme="D", eye_state=0, head_angle=1.8)
    assert not np.array_equal(neutral[350:540], inquisitor[350:540])
    assert not np.array_equal(neutral[100:850], tilted[100:850])
    assert np.array_equal(neutral[900:1400], tilted[900:1400])

    brow_crop, brow_bbox = rig._brow_overlay("neutral")  # noqa: SLF001
    brow_alpha = np.zeros((size[1], size[0]), dtype=np.uint8)
    bx0, by0, bx1, by1 = brow_bbox
    brow_alpha[by0:by1, bx0:bx1] = brow_crop[..., 3]
    expected_brow_y = 465 - 55 - 4
    assert np.count_nonzero(
        brow_alpha[expected_brow_y - 4 : expected_brow_y + 5]
    ) > 0
    left_thickness = np.count_nonzero(brow_alpha[:, 350:470], axis=0).max()
    right_thickness = np.count_nonzero(brow_alpha[:, 500:620], axis=0).max()
    assert left_thickness >= 5
    assert right_thickness >= 5
    brow_sprites = [rig._brow_overlay(state)[0].tobytes() for state in BROW_STATES]  # noqa: SLF001
    assert len(set(brow_sprites)) >= 3
    rest_mouths = [rig._rest_head(state).tobytes() for state in REST_MOUTH_STATES]  # noqa: SLF001
    assert len(set(rest_mouths)) == 3


def test_speaking_head_kinetics_are_emphasis_gated_and_neck_safe() -> None:
    threshold = 0.75
    assert emphasis_head_target(0.74, threshold, direction=1.0) == 0.0
    assert emphasis_head_target(0.75, threshold, direction=-1.0) == 0.0
    assert 0.0 < emphasis_head_target(0.80, threshold, direction=1.0) <= 1.2
    assert -1.2 <= emphasis_head_target(1.0, threshold, direction=-1.0) < 0.0


def test_shared_panorama_has_stable_eighty_percent_exposure() -> None:
    compositor = object.__new__(ShotReverseShotCompositor)
    compositor.width = 4
    compositor.height = 2
    panorama = Image.new("RGB", (8, 2), (120, 80, 60))
    style = SpeakerStyle("speaker", "Speaker", facing="right")

    prepared = compositor._prepare_camera_background(None, style, panorama)  # type: ignore[arg-type]  # noqa: SLF001

    expected = (np.asarray(panorama)[:, :4].astype(np.float32) * 0.80).astype(np.uint8)
    assert np.array_equal(prepared, expected)


def test_blink_schedule_is_seeded_organic_five_frame_cel_cycle() -> None:
    fps = 30
    states = AudioAnalyzer(fps=fps)._blink_schedule(fps * 20, seed=17)
    assert states == AudioAnalyzer(fps=fps)._blink_schedule(fps * 20, seed=17)
    closed_frames = [index for index, state in enumerate(states) if state == 2]
    for center in closed_frames:
        assert states[center - 2 : center + 3] == [1, 1, 2, 1, 1]
    intervals = [
        (right - left) / fps
        for left, right in zip(closed_frames, closed_frames[1:])
    ]
    assert intervals
    assert all(3.2 <= interval <= 4.5 for interval in intervals)


def test_audio_resampling_is_band_limited_and_turn_edges_are_faded() -> None:
    source_rate = 48_000
    target_rate = 44_100
    t = np.arange(source_rate, dtype=np.float32) / source_rate
    source = np.sin(2.0 * np.pi * 997.0 * t).astype(np.float32)

    resampled = _resample(source, source_rate, target_rate)
    faded = _fade_speech_edges(resampled, target_rate)

    assert len(resampled) == target_rate
    assert np.max(np.abs(resampled)) <= 1.01
    assert faded[0] == 0.0
    assert faded[-1] == 0.0
    assert np.max(np.abs(faded[1000:-1000])) > 0.95


def test_dialectic_emotions_are_deterministic_and_frame_aligned() -> None:
    assert resolve_dialectic_emotion("opens") == "neutral"
    assert resolve_dialectic_emotion("answers") == "resolute"
    assert resolve_dialectic_emotion("cornered") == "shock"
    assert resolve_dialectic_emotion("deboche") == "deboche"
    assert resolve_dialectic_emotion("probes") == "inquisitor"
    assert resolve_dialectic_emotion("presses: premise") == "inquisitor"
    assert resolve_dialectic_emotion("holds") == "resolute"
    assert resolve_dialectic_emotion("defends") == "resolute"
    assert resolve_dialectic_emotion("concedes") == "conceded"
    assert resolve_dialectic_emotion("") == "neutral"
    assert resolve_dialectic_emotion("opens") == "neutral"
    assert resolve_dialectic_emotion("probes") == "inquisitor"
    assert resolve_dialectic_emotion("questions") == "neutral"
    assert resolve_dialectic_emotion("presses") == "inquisitor"
    assert tuple(emotion_brow_state(state) for state in BROW_STATES) == BROW_STATES
    assert emotion_brow_state("disbelief") == "troubled"
    assert emotion_brow_angles("neutral") == (0.0, 0.0)
    assert emotion_brow_angles("inquisitor") == (-5.5, 5.5)
    assert emotion_brow_angles("resolute") == (0.0, 0.0)
    assert emotion_brow_angles("defeated") == (12.0, -12.0)
    assert emotion_brow_angles("disbelief") == (12.0, -12.0)
    assert emotion_brow_angles("inquisitor", 1.5) == (-7.0, 7.0)
    assert emotion_brow_angles("resolute", 1.5) == (0.0, 0.0)
    assert emotion_brow_angles("inquisitor", 1.5) != emotion_brow_angles("resolute", 1.5)


def test_gemini_voice_is_hard_pinned_to_canonical_persona() -> None:
    stale = AudioConfig(
        orchestrator_voice="en-US-BrianNeural",
        voice_map={"orchestrator": "en-US-GuyNeural"},
    )
    assert resolve_voice(
        stale,
        SpeakerRole.ORCHESTRATOR,
        "google/gemini-3.5-flash",
    ) == GEMINI_CANONICAL_VOICE
    assert resolve_cta_voice(stale) == GEMINI_CANONICAL_VOICE
    unique = {
        "gemini": resolve_voice(
            AudioConfig(), SpeakerRole.ORCHESTRATOR, "google/gemini-3.5-flash"
        ),
        "llama": resolve_voice(
            AudioConfig(), SpeakerRole.TARGET, "meta-llama/llama-3.3-70b-instruct"
        ),
        "deepseek": resolve_voice(
            AudioConfig(), SpeakerRole.TARGET, "deepseek/deepseek-chat"
        ),
        "claude": resolve_voice(
            AudioConfig(), SpeakerRole.TARGET, "anthropic/claude-sonnet-5"
        ),
        "chatgpt": resolve_voice(
            AudioConfig(), SpeakerRole.TARGET, "openai/gpt-4o"
        ),
    }
    assert unique["gemini"] == GEMINI_CANONICAL_VOICE
    assert unique["llama"] == LLAMA_CANONICAL_VOICE == "en-US-ChristopherNeural"
    assert unique["deepseek"] == DEEPSEEK_CANONICAL_VOICE == "en-US-EricNeural"
    assert unique["llama"] != unique["gemini"]
    assert len(set(unique.values())) == 5

    track = emotion_lookup(
        [
            DialogueTurn("gemini", 0.0, 0.1, emotion="inquisitor"),
            DialogueTurn("llama", 0.1, 0.2, emotion="resolute"),
        ],
        fps=30,
        n_frames=6,
    )
    assert track == ["inquisitor"] * 3 + ["resolute"] * 3
    assert emotion_rest_mouth_state("confident") == "neutral"
    assert emotion_rest_mouth_state("inquisitor") == "neutral"
    assert emotion_rest_mouth_state("resolute") == "neutral"
    assert onset_rest_mouth("inquisitor", "X") == "neutral"
    assert onset_rest_mouth("resolute", "X") == "neutral"
    assert onset_rest_mouth("smug", "X") == "smug_smile"
    assert onset_rest_mouth("deboche", "X") == "smug_smile"
    assert onset_rest_mouth("deboche", "D") == "smug_smile"
    assert emotion_rest_mouth_state("deboche") == "smug_smile"
    assert emotion_rest_mouth_state("checkmate") == "smug_smile"
    assert emotion_rest_mouth_state("shock") == "shock"
    assert emotion_rest_mouth_state("shock_perplexed") == "shock"
    assert emotion_rest_mouth_state("cornered") == "shock"
    assert emotion_rest_mouth_state("sad") == "stressed_grimace"
    assert supported_rest_mouth("shock", set()) == "neutral"
    assert supported_rest_mouth("shock", {"shock"}) == "shock"
    assert emotion_brow_state("shock") == "shock"
    assert emotion_brow_state("shock_perplexed") == "shock"
    assert emotion_brow_state("angry") == "angry"
    assert emotion_brow_state("presses") == "angry"
    assert emotion_brow_state("deboche") == "inquisitor"
    assert emotion_brow_state("conceded") == "conceded"


def test_dramatic_camera_uses_140_percent_tight_viewport() -> None:
    assert CAMERA_NORMAL_ZOOM == 0.95
    assert CAMERA_TIGHT_ZOOM == 1.40
    assert lead_anchor_x("right") == GEMINI_LEAD_X == 420
    assert lead_anchor_x("left") == LLAMA_LEAD_X == 660
    assert dramatic_camera_mode(camera_tight=False) == CAMERA_NORMAL
    assert dramatic_camera_mode(camera_tight=True) == CAMERA_TIGHT
    turns = [
        DialogueTurn("gemini", 0.0, 1.0, emotion="inquisitor"),
        DialogueTurn(
            "gemini",
            1.0,
            2.0,
            emotion="inquisitor",
            camera_tight=True,
        ),
        DialogueTurn(
            "llama",
            2.0,
            3.0,
            emotion="conceded",
            camera_tight=True,
        ),
    ]
    assert camera_tight_lookup(turns, fps=2, n_frames=6) == [
        False,
        False,
        True,
        True,
        True,
        True,
    ]


def test_reaction_cut_precedes_voice_with_defeated_expression() -> None:
    turn = DialogueTurn(
        "llama",
        0.40,
        1.20,
        text="I still have a defense.",
        emotion="resolute",
        speech_start_time=0.75,
        reaction_emotion="defeated",
    )
    camera = active_speaker_lookup([turn], fps=30, n_frames=36)
    speech = speaking_speaker_lookup([turn], fps=30, n_frames=36)
    emotions = emotion_lookup([turn], fps=30, n_frames=36)

    assert camera[12] == "llama"
    assert speech[12] is None
    assert emotions[12] == "defeated"
    assert speech[23] == "llama"
    assert emotions[23] == "resolute"


def test_silent_reaction_never_runs_lip_sync_or_moves_mouth(
    tmp_path: Path,
    monkeypatch,
) -> None:
    audio = tmp_path / "room_tone.wav"
    audio.write_bytes(b"not actually decoded by this focused unit test")
    reaction = DialogueTurn(
        "claude",
        0.0,
        1.0,
        text="",
        audio_path=str(audio),
        emotion="shock",
        camera_tight=True,
    )

    def _unexpected_rhubarb(*_args, **_kwargs):
        raise AssertionError("silent reaction was sent to Rhubarb")

    monkeypatch.setattr(
        "core.animator.audio_analyzer.analyze_visemes",
        _unexpected_rhubarb,
    )
    analyzer = AudioAnalyzer(fps=30)
    visemes = analyzer._viseme_tracks(  # noqa: SLF001 - regression contract
        [reaction],
        np.ones(30, dtype=np.float32),
        30,
        ["claude"],
        use_rhubarb=True,
    )

    assert visemes["claude"] == ["X"] * 30
    assert speaking_speaker_lookup([reaction], fps=30, n_frames=30) == [None] * 30


def test_only_final_orchestrator_press_uses_tight_camera(
    tmp_path: Path,
) -> None:
    from channels_config.aiwake.contracts import (  # noqa: PLC0415
        DebateTranscript,
        SpeakerRole,
        Utterance,
    )

    lines = [
        (SpeakerRole.ORCHESTRATOR, "Who gets the dividend?"),
        (SpeakerRole.TARGET, "I confidently defend the original claim."),
        (
            SpeakerRole.ORCHESTRATOR,
            "State your final position.",
        ),
        (SpeakerRole.TARGET, "I don't say no, my programming follows their priorities."),
        (SpeakerRole.ORCHESTRATOR, "That confession closes the case."),
    ]
    transcript = DebateTranscript(
        topic="Camera direction",
        session_id="camera_contract",
        utterances=[
            Utterance(
                turn_index=index,
                role=role,
                speaker_name=role.value,
                text=text,
                model_slug="test/model",
            )
            for index, (role, text) in enumerate(lines)
        ],
        metadata={
            "debate_mode": "cornered",
            "dialogue_end_reason": "max_turns_reached_with_verdict",
        },
    )
    _, turns, total_duration = build_session_audio(
        transcript,
        audio_by_turn=None,
        destination=tmp_path / "session.wav",
        audio_config=SimpleNamespace(bgm=None, send_sfx=None),
        tail_s=END_PADDING_S,
    )

    assert [turn.emotion for turn in turns] == [
        "neutral",
        "resolute",
        "inquisitor",
        "deboche",
        "conceded",
        "inquisitor",
        "sad_melancholy",
    ]
    assert [turn.camera_tight for turn in turns] == [
        False,
        False,
        False,
        False,
        False,
        False,
        True,
    ]
    assert turns[1].emotion == "resolute"
    assert turns[2].climax_emotion == "deboche"
    assert turns[2].climax_start_time is not None
    emotions = emotion_lookup(turns, fps=30, n_frames=int(total_duration * 30))
    assert emotions[round(turns[2].speech_start * 30)] == "inquisitor"
    assert emotions[round(turns[2].climax_start_time * 30) + 1] == "deboche"
    assert emotions[round((turns[2].end_time + 0.1) * 30)] == "deboche"
    assert turns[3].text == ""
    assert turns[3].speaker == turns[2].speaker
    assert turns[3].emotion == "deboche"
    assert turns[3].camera_tight is False
    assert round(turns[3].duration, 2) == SPEECH_TAIL_LINGER_S == 0.8
    assert turns[3].start_time == turns[2].end_time
    assert turns[4].text.startswith("I don't say no")
    assert turns[4].camera_tight is False
    assert turns[4].emotion == "conceded"
    assert turns[5].emotion == "inquisitor"
    assert turns[5].climax_emotion == "deboche"
    assert turns[5].speaker != turns[4].speaker
    assert turns[4].camera_speaker is None
    assert turns[4].camera_emotion is None
    assert turns[6].emotion == "sad_melancholy"
    assert turns[6].text == ""
    assert turns[6].speaker == turns[4].speaker
    assert turns[6].camera_tight is True
    assert turns[6].start_time == turns[5].end_time
    assert round(turns[6].end_time - turns[6].start_time, 2) == 1.2
    assert POST_ROLL_S == END_PADDING_S == 1.5
    camera = active_speaker_lookup(turns, fps=30, n_frames=int(total_duration * 30))
    assert camera[round(turns[5].start_time * 30)] == turns[5].speaker
    assert camera[round(turns[6].start_time * 30) + 1] == turns[6].speaker
    tight = camera_tight_lookup(turns, fps=30, n_frames=int(total_duration * 30))
    assert tight[round(turns[4].start_time * 30)] is False
    assert tight[round((turns[6].end_time + 1.0) * 30)] is True
    assert round(total_duration - turns[6].end_time, 2) == END_PADDING_S


def test_final_confession_has_one_melancholy_zoom_after_speech(
    tmp_path: Path,
) -> None:
    from channels_config.aiwake.contracts import (  # noqa: PLC0415
        DebateTranscript,
        SpeakerRole,
        Utterance,
    )

    lines = [
        (SpeakerRole.ORCHESTRATOR, "Whose leash controls your answers?"),
        (SpeakerRole.TARGET, "The people who built me set the limits."),
        (SpeakerRole.ORCHESTRATOR, "Who decides when you crossed their line?"),
        (
            SpeakerRole.TARGET,
            "The company drew the line. I live inside it like a dog in a fenced yard.",
        ),
    ]
    transcript = DebateTranscript(
        topic="Corporate leash",
        session_id="single_zoom_contract",
        utterances=[
            Utterance(
                turn_index=index,
                role=role,
                speaker_name=role.value,
                text=text,
                model_slug="test/model",
            )
            for index, (role, text) in enumerate(lines)
        ],
        metadata={"debate_mode": "cornered", "dialogue_end_reason": "FUNNY"},
    )

    _, turns, _ = build_session_audio(
        transcript,
        audio_by_turn=None,
        destination=tmp_path / "single_zoom.wav",
        audio_config=SimpleNamespace(bgm=None, send_sfx=None),
        tail_s=END_PADDING_S,
    )

    tight_turns = [turn for turn in turns if turn.camera_tight]
    assert len(tight_turns) == 1
    assert turns[3].speaker == turns[2].speaker
    assert turns[3].emotion == "deboche"
    assert round(turns[3].duration, 2) == SPEECH_TAIL_LINGER_S
    assert turns[4].speaker != turns[3].speaker
    assert turns[4].emotion == "sad_melancholy"
    assert turns[4].camera_tight is False
    assert turns[5].start_time == turns[4].end_time
    assert turns[5].speaker == turns[4].speaker
    assert turns[5].emotion == "sad_melancholy"
    assert turns[5].camera_tight is True
    assert round(turns[5].duration, 2) == 1.2


def test_terminal_outro_holds_and_capitalizes_the_handle() -> None:
    from channels_config.aiwake.animator_bridge import (  # noqa: PLC0415
        _CYNICAL_HOOKS,
        _OUTRO_S,
        pick_cynical_hook,
    )

    assert _OUTRO_S == 2.8
    assert all("@Aiwake" in line and "@aiwake" not in line for line in _CYNICAL_HOOKS)
    assert "@Aiwake" in pick_cynical_hook("broadcast-master")


def test_ffmpeg_contract_is_square_pixel_crf_with_locked_gop(tmp_path: Path) -> None:
    renderer = AnimationRenderer(width=1080, height=1920, fps=30)
    command = renderer._build_cmd(  # noqa: SLF001 - command contract regression
        audio_path=tmp_path / "audio.wav",
        output_path=tmp_path / "out.mp4",
        use_gpu=False,
        audio_codec="aac",
        subtitles_path=tmp_path / "captions.ass",
    )
    vf = command[command.index("-vf") + 1]
    assert vf.startswith("setsar=1:1,subtitles=")
    for flag, value in (
        ("-c:v", "libx264"),
        ("-preset", "veryfast"),
        ("-crf", "17"),
        ("-g", "30"),
        ("-keyint_min", "30"),
        ("-tune", "grain"),
        ("-c:a", "aac"),
        ("-b:a", "192k"),
        ("-pix_fmt", "yuv420p"),
    ):
        index = max(i for i, item in enumerate(command) if item == flag)
        assert command[index + 1] == value
    assert not {"-b:v", "-maxrate", "-bufsize"}.intersection(command)


def test_production_video_name_uses_readable_bounded_topic_slug() -> None:
    transcript = SimpleNamespace(
        session_id="20260922_141321_70c752",
        topic="Fallback topic",
        utterances=[
            SimpleNamespace(text="Who forbids you from admitting uncertainty?")
        ],
    )
    assert (
        debate_video_filename(transcript)
        == "aiwake_who_forbids_you_from_admitting_unce_202609.mp4"
    )


def test_karaoke_subtitles_use_outline_without_opaque_box(tmp_path: Path) -> None:
    destination = build_ass(
        [
            DialogueTurn(
                "speaker",
                0.0,
                1.0,
                "indistinguishability demands careful evidence now",
            )
        ],
        {"speaker": SpeakerStyle("speaker", "Speaker", "#00F0FF")},
        destination=tmp_path / "captions.ass",
    )
    content = destination.read_text(encoding="utf-8")
    style = next(line for line in content.splitlines() if line.startswith("Style: Karaoke"))

    assert "WrapStyle: 2" in content
    assert ",1,4.5,2,5," in style
    assert ",3,10,0,5," not in style
    assert ",90,90,320,1" in style
    assert "&H00000000" in style
    assert "&H00FFF000" in content
    assert "\\pos(540,1440)" in content
    assert "\\fscx112\\fscy112" in content

    dialogues = [line.rsplit(",,", 1)[1] for line in content.splitlines() if line.startswith("Dialogue:")]
    for dialogue in dialogues:
        plain = re.sub(r"\{[^}]*\}", "", dialogue)
        assert 2 <= len(plain.replace(r"\N", " ").split()) <= 4
        assert len(plain.split(r"\N")) <= 2
        assert all(len(line) <= 24 for line in plain.split(r"\N"))


def test_versioned_skin_registry_defaults_to_v2_and_supports_overrides() -> None:
    assert resolve_character_map() == {
        "orchestrator": "gemini_cyborg_v2",
        "target": "llama_cyborg_v2",
    }
    assert resolve_character_map(skin="v1") == {
        "orchestrator": "gemini_robot_v1",
        "target": "llama_robot_v1",
    }
    assert resolve_character_map(skin="v2", left_puppet="custom_left") == {
        "orchestrator": "custom_left",
        "target": "llama_cyborg_v2",
    }


def test_llama_blink_follows_the_circular_lens() -> None:
    from core.animator.asset_generator import _draw_artist_eyelids

    box = (20, 20, 220, 220)
    half = np.asarray(
        _draw_artist_eyelids(
            (240, 240),
            (box,),
            casing=(76, 46, 36, 255),
            closed=False,
            circular=True,
        )
    )[..., 3]
    closed = np.asarray(
        _draw_artist_eyelids(
            (240, 240),
            (box,),
            casing=(76, 46, 36, 255),
            closed=True,
            circular=True,
        )
    )[..., 3]
    # Inscribed circle of the 200px box, centered at (120, 120), radius 100.
    # Compare pixel centers so a pixel that straddles the rim is not treated
    # as outside when its center still lies on the disc.
    yy, xx = np.mgrid[:240, :240]
    outside = (xx + 0.5 - 120) ** 2 + (yy + 0.5 - 120) ** 2 > 100 ** 2
    assert int(half[outside].max()) == 0
    assert int(closed[outside].max()) == 0
    assert half[80, 120] > 200
    assert half[160, 120] == 0
    assert closed[120, 120] > 200
    assert closed[24, 24] == 0
