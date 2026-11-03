"""Library registration keeps tests, silence, and short clips out."""
from __future__ import annotations

from products.aiwake.pipeline import session_is_library_eligible


def test_real_short_is_eligible() -> None:
    assert session_is_library_eligible(
        session_id="20261030_061706_f1265c",
        video_name="aiwake_what_s_it_like_being_as_disposable_202610.mp4",
        duration_s=72.5,
        has_audio=True,
    )


def test_test_sessions_short_clips_and_silence_stay_out() -> None:
    assert not session_is_library_eligible(
        session_id="test_render",
        video_name="aiwake_debate.mp4",
        duration_s=40,
        has_audio=True,
    )
    assert not session_is_library_eligible(
        session_id="20261030_061706_f1265c",
        video_name="test_v3_iteration.mp4",
        duration_s=40,
        has_audio=True,
    )
    assert not session_is_library_eligible(
        session_id="20261030_061706_f1265c",
        video_name="aiwake_debate.mp4",
        duration_s=14.9,
        has_audio=True,
    )
    assert not session_is_library_eligible(
        session_id="20261030_061706_f1265c",
        video_name="aiwake_debate.mp4",
        duration_s=80,
        has_audio=False,
    )
