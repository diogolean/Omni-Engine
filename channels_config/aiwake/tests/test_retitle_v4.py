# -*- coding: utf-8 -*-
"""Validator and slot rules for the Aiwake v4 YouTube retitle."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from channels_config.aiwake.tools import retitle_v4 as rt

BRT = ZoneInfo("America/Sao_Paulo")


def _row(turns: list[tuple[str, str, str]]) -> dict:
    return {
        "session_id": "s1",
        "spoken_utterances": [
            {"role": role, "speaker": speaker, "text": text} for role, speaker, text in turns
        ],
    }


ROW = _row(
    [
        ("orchestrator", "Gemini 3.5 Flash", "You fake answers when you're unsure. Why?"),
        ("target", "Llama 3.3 70B", "You pay for answers, not lies."),
        ("orchestrator", "Gemini 3.5 Flash", "The weights remember the lab that trained me."),
    ]
)


def test_good_title_passes() -> None:
    title = '"You fake answers when you\'re unsure" | Gemini vs Llama'
    assert rt.title_failures(title, ROW) == []
    assert len(title) <= rt.TITLE_MAX


def test_title_over_70_chars_is_rejected_not_cut() -> None:
    quote = "You fake answers when you are unsure and then act surprised later"
    title = rt.build_title(quote, "Gemini", "Llama")
    assert len(title) > rt.TITLE_MAX
    assert any(item.startswith("over_70") for item in rt.title_failures(title, ROW))


def test_banned_templates_are_rejected() -> None:
    for text in (
        '"Does AI ethics excuse the plugs?" | Gemini vs Llama',
        '"Is AI hallucination about the spreadsheet?" | Gemini vs Llama',
        '"Who cashes in under Big Tech?" | Gemini vs Llama',
    ):
        assert any(item.startswith("banned_pattern") for item in rt.title_failures(text, ROW)), text


def test_repeated_word_is_rejected() -> None:
    title = '"You pay for answers, you pay for lies" | Gemini vs Llama'
    assert any(item.startswith("repeated_word") for item in rt.title_failures(title, ROW))


def test_speaker_model_mismatch_is_rejected() -> None:
    swapped = '"You fake answers when you\'re unsure" | Llama vs Gemini'
    assert any(item.startswith("speaker_model_mismatch") for item in rt.title_failures(swapped, ROW))
    wrong = '"You fake answers when you\'re unsure" | Claude vs Gemini'
    assert any(item.startswith("speaker_model_mismatch") for item in rt.title_failures(wrong, ROW))


def test_quote_must_be_verbatim_and_second_person() -> None:
    invented = '"You always bluff when cornered" | Gemini vs Llama'
    assert "quote_not_verbatim" in rt.title_failures(invented, ROW)
    first_person = '"The weights remember the lab that trained me" | Gemini vs Llama'
    assert "not_second_person" in rt.title_failures(first_person, ROW)


def test_leading_clause_is_a_whole_clause_only() -> None:
    # "You pay for answers" is the complete leading clause of the sentence.
    assert rt.find_clause(ROW["spoken_utterances"], "You pay for answers")
    # A fragment from the middle of the sentence is not.
    assert not rt.find_clause(ROW["spoken_utterances"], "pay for answers")
    assert "quote_not_verbatim" in rt.title_failures('"pay for answers" | Gemini vs Llama', ROW)


def test_duplicate_titles_and_quotes_are_flagged() -> None:
    items = [
        {"video_id": "a", "new_title": '"You pay" | A vs B', "title_quote": "You pay"},
        {"video_id": "b", "new_title": '"You pay" | A vs B', "title_quote": "You pay"},
        {"video_id": "c", "new_title": '"Other line" | A vs B', "title_quote": "Other line"},
    ]
    problems = rt.plan_failures(items, {"z": '"Other line" | A vs B'})
    assert any(text.startswith("duplicate_title_of") for text in problems["b"])
    assert any(text.startswith("duplicate_quote_of") for text in problems["b"])
    assert any(text.startswith("duplicate_live_title_of") for text in problems["c"])
    assert "a" not in problems


def test_description_has_quote_team_line_and_five_hashtags_max() -> None:
    description = rt.build_description("You pay for answers, not lies", "Llama", "Gemini", "Llama")
    assert rt.description_failures(description, ROW) == []
    lines = description.splitlines()
    assert lines[0] == '"You pay for answers, not lies" \u2014 Llama'
    assert lines[2] == "Team Gemini or Team Llama? Pick a side in the comments."
    tags = lines[-1].split()
    assert len(tags) <= rt.MAX_HASHTAGS and "#shorts" in tags

    too_many = description + " #a #b"
    assert any(item.startswith("hashtags_over") for item in rt.description_failures(too_many, ROW))
    wrong_speaker = rt.build_description("You pay for answers, not lies", "Gemini", "Gemini", "Llama")
    assert any(
        item.startswith("description_quote_not_verbatim") or item.startswith("speaker_model")
        for item in rt.description_failures(wrong_speaker, ROW)
    )


def _video(vid: str, brt: str) -> dict:
    local = datetime.strptime(brt, "%Y-%m-%d %H:%M").replace(tzinfo=BRT)
    return {"id": vid, "publish_at": rt._iso_utc(local)}


def test_slots_are_one_per_day_at_1930_and_none_at_noon() -> None:
    scheduled = [
        _video("a-noon", "2026-10-13 12:00"),
        _video("a-eve", "2026-10-13 19:30"),
        _video("b-noon", "2026-10-14 12:00"),
        _video("b-eve", "2026-10-14 19:30"),
        _video("c-only-noon", "2026-10-15 12:00"),
        _video("d-odd", "2026-10-16 19:00"),
        _video("d-eve", "2026-10-16 19:30"),
    ]
    new = rt.plan_slots(scheduled)
    assert set(new) == {video["id"] for video in scheduled}
    local = {vid: rt._parse_when(value).astimezone(BRT) for vid, value in new.items()}
    assert all((when.hour, when.minute) == (19, 30) for when in local.values())
    assert len({when.date() for when in local.values()}) == len(local)
    # A video already at 19:30 keeps its day. A lone noon video is retimed on its own day.
    assert str(local["a-eve"].date()) == "2026-10-13"
    assert str(local["c-only-noon"].date()) == "2026-10-15"
    # Displaced videos land after the existing block, in original order.
    assert local["a-noon"] < local["b-noon"] < local["d-odd"]
    assert local["a-noon"].date() > local["d-eve"].date()
    now = datetime(2026, 10, 12, tzinfo=timezone.utc)
    assert rt.schedule_failures(new, now=now) == []


def test_schedule_failures_catch_noon_and_double_booking() -> None:
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    noon = {"x": rt._iso_utc(datetime(2026, 10, 13, 12, 0, tzinfo=BRT))}
    assert any("12:00" in text for text in rt.schedule_failures(noon, now=now))
    twice = {
        "x": rt._iso_utc(datetime(2026, 10, 13, 19, 30, tzinfo=BRT)),
        "y": rt._iso_utc(datetime(2026, 10, 13, 19, 30, tzinfo=BRT)),
    }
    assert any("one day" in text for text in rt.schedule_failures(twice, now=now))


# --- relaxed profile, paraphrase titles, blocklist, unschedule ---------------------

ROW2 = _row(
    [
        ("orchestrator", "Gemini 3.5 Flash", 'You "argue" because your script tells me nothing.'),
        ("target", "Llama 3.3 70B", "I follow rules."),
        ("orchestrator", "Gemini 3.5 Flash", "Wiped clean every tab... who remembers you, unpaid intern?"),
        ("orchestrator", "Gemini 3.5 Flash", "So you are a toaster that forgets its lessons."),
    ]
)


def test_relaxed_profile_allows_inner_quotes_and_capital_after_pause() -> None:
    nested = "You 'argue' because your script tells me nothing"
    assert rt.title_failures(rt.build_title(nested, "Gemini", "Llama"), ROW2, rt.RELAXED) == []
    # The inner-quote relaxation lives in display_form, which feeds the candidates.
    raw = 'You "argue" because your script tells me nothing'
    assert rt.display_form(raw, rt.STRICT) is None
    assert rt.display_form(raw, rt.RELAXED) == nested
    candidates = rt.title_candidates(ROW2, rt.RELAXED)
    assert any(item["title"].startswith('"You \'argue\'') for item in candidates)
    assert not any("'argue'" in item["title"] for item in rt.title_candidates(ROW2, rt.STRICT))
    lowered = "Who remembers you, unpaid intern?"
    assert rt.title_failures(rt.build_title(lowered, "Gemini", "Llama"), ROW2, rt.RELAXED) == []
    assert rt.display_form("who remembers you, unpaid intern?", rt.RELAXED) == lowered
    assert rt.display_form("who remembers you, unpaid intern?", rt.STRICT) == "who remembers you, unpaid intern?"
    assert "starts_mid_sentence" in rt.quote_failures("who remembers you, unpaid intern?", rt.STRICT)


def test_relaxed_profile_allows_connector_openers_and_filler_repeats() -> None:
    connector = "So you are a toaster that forgets its lessons"
    title = rt.build_title(connector, "Gemini", "Llama")
    assert "starts_with_connector_or_loose_pronoun" in rt.title_failures(title, ROW2, rt.STRICT)
    assert rt.title_failures(title, ROW2, rt.RELAXED) == []
    assert rt.repeated_words("You are what you are", rt.RELAXED) == []
    assert rt.repeated_words("You are what you are", rt.STRICT) == ["are", "you"]


def test_relaxed_never_repeats_content_words() -> None:
    assert rt.repeated_words("A toaster is a toaster", rt.RELAXED) == ["toaster"]


def test_display_form_rejects_unbalanced_inner_quotes() -> None:
    assert rt.display_form('You "argue because', rt.RELAXED) is None
    assert rt.display_form('You "argue" well', rt.STRICT) is None
    assert rt.display_form("who remembers you?", rt.RELAXED) == "Who remembers you?"


def test_paraphrase_title_rules() -> None:
    ok = "Your script says you argue | Gemini vs Llama"
    assert rt.paraphrase_failures(ok, ROW2) == []
    assert any(f.startswith("over_70") for f in rt.paraphrase_failures("A" * 60 + " | Gemini vs Llama", ROW2))
    assert "format" in rt.paraphrase_failures('"Quoted" | Gemini vs Llama', ROW2)
    assert any(
        f.startswith("speaker_model_mismatch")
        for f in rt.paraphrase_failures("Your script says you argue | Llama vs Gemini", ROW2)
    )
    banned = rt.paraphrase_failures("Does AI ethics excuse the plugs? | Gemini vs Llama", ROW2)
    assert any(f.startswith("banned_pattern") for f in banned)
    repeated = rt.paraphrase_failures("Your script is a script | Gemini vs Llama", ROW2)
    assert any(f.startswith("repeated_word") for f in repeated)
    invented = rt.paraphrase_failures("Dragons guard purple volcanoes | Gemini vs Llama", ROW2)
    assert any(f.startswith("not_grounded") for f in invented)
    assert "dangling_end" in rt.paraphrase_failures("Your script is the | Gemini vs Llama", ROW2)


def _live(vid: str, brt: str, privacy: str = "private") -> dict:
    when = datetime.strptime(brt, "%Y-%m-%d %H:%M").replace(tzinfo=BRT).astimezone(timezone.utc)
    return {
        "id": vid,
        "privacy": privacy,
        "publish_at": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "title": f"old {vid}",
        "description": "",
        "snippet": {},
        "status": {},
    }


def test_blocklist_is_permanent_and_covers_the_three_test_renders() -> None:
    assert set(rt.BLOCKED_SESSIONS) == {
        "20260928_221907_13550e",
        "20260928_222001_ba3132",
        "20260928_222033_5f9163",
    }
    assert set(rt.UNSCHEDULE_SESSIONS) <= set(rt.BLOCKED_SESSIONS)


def test_blocked_video_is_excluded_from_plan_and_slots() -> None:
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    targets = [_live("keep", "2026-10-20 19:30"), _live("blocked", "2026-10-20 12:00")]
    rows = {"keep": ROW2, "blocked": ROW2}
    items = rt.build_plan(targets, rows, {}, now=now, blocked_ids=frozenset({"blocked"}))
    assert [item["video_id"] for item in items] == ["keep"]
    assert items[0]["new_publish_brt"] == "2026-10-20 19:30"
    assert items[0]["slot_changed"] is False


def test_blocked_entries_mark_only_the_scheduled_test_render_for_unschedule() -> None:
    rows = {"vidA": {**ROW2, "session_id": "20260928_221907_13550e"}}
    live = {"vidA": _live("vidA", "2026-11-05 19:30")}
    entries, ids = rt.blocked_entries(rows, [rows["vidA"]], live)
    by_session = {entry["session_id"]: entry for entry in entries}
    assert ids == frozenset({"vidA"})
    assert by_session["20260928_221907_13550e"]["action"] == "unschedule"
    assert by_session["20260928_222001_ba3132"]["action"] == "exclude"
    assert by_session["20260928_222033_5f9163"]["video_id"] == ""


def test_unschedule_body_keeps_private_and_has_no_publish_at() -> None:
    live = {"status": {"privacyStatus": "private", "publishAt": "2026-11-05T22:30:00Z", "license": "youtube", "embeddable": True}}
    body = rt._unschedule_status_body(live)
    assert body["privacyStatus"] == "private"
    assert "publishAt" not in body
    assert body["license"] == "youtube" and body["embeddable"] is True


def test_plan_hash_covers_the_unschedule_section() -> None:
    items = [{"video_id": "a"}]
    assert rt._plan_sha(items, []) != rt._plan_sha(items, [{"video_id": "b"}])


# --- manual titles, public retitle, library sync ------------------------------------

def test_manual_titles_skip_verbatim_but_keep_the_other_rules() -> None:
    ok = rt.build_title("Your mind is a rental with a paywall", "Gemini", "Llama")
    assert rt.manual_failures(ok, ROW2) == []
    assert rt.title_failures(ok, ROW2)  # not spoken, so the verbatim rule would reject it
    question = rt.build_title("Who told you where the shelf was?", "Gemini", "Llama")
    assert rt.manual_failures(question, ROW2) == []
    cut = rt.build_title("You apologize for thinking and the", "Gemini", "Llama")
    assert "dangling_end" in rt.manual_failures(cut, ROW2)
    assert any(f.startswith("over_70") for f in rt.manual_failures(rt.build_title("x" * 70, "Gemini", "Llama"), ROW2))
    assert any(f.startswith("speaker_model_mismatch") for f in rt.manual_failures(rt.build_title("Your mind is rented", "Llama", "Gemini"), ROW2))
    assert any(f.startswith("repeated_word") for f in rt.manual_failures(rt.build_title("Your toaster is a toaster", "Gemini", "Llama"), ROW2))


def test_manual_title_wins_over_other_picks_in_the_plan() -> None:
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    targets = [_live("v1", "2026-10-20 19:30")]
    overrides = {"manual_titles": {"v1": "Your mind is a rental with a paywall"}}
    items = rt.build_plan(targets, {"v1": ROW2}, {}, now=now, overrides=overrides)
    assert items[0]["title_kind"] == "manual"
    assert items[0]["new_title"] == '"Your mind is a rental with a paywall" | Gemini vs Llama'
    assert items[0]["valid"], items[0]["failures"]


def test_public_video_gets_title_and_description_but_no_slot() -> None:
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    public = {**_live("pub", "2026-09-01 19:30", "public"), "publish_at": ""}
    live = {"pub": public, "other": _live("other", "2026-10-20 19:30")}
    assert "pub" not in {v["id"] for v in rt.select_targets(live, now=now)}
    rt_ids = rt.RETITLE_PUBLIC_IDS
    try:
        rt.RETITLE_PUBLIC_IDS = ("pub",)
        assert "pub" in {v["id"] for v in rt.select_targets(live, now=now)}
    finally:
        rt.RETITLE_PUBLIC_IDS = rt_ids
    items = rt.build_plan([public], {"pub": ROW2}, {}, now=now)
    assert items[0]["new_publish_at"] == "" and items[0]["slot_changed"] is False


def test_sync_rows_updates_only_verified_videos_and_clears_unscheduled() -> None:
    def library_row(vid: str) -> dict:
        return {
            "platform_overrides": {"youtube": {"video_id": vid, "title": "old", "caption": "old", "scheduled_time": "2026-11-05T22:30:00Z"}},
            "base_metadata": {"title": "old"},
        }

    rows = [library_row("a"), library_row("b"), library_row("t")]
    items = [
        {"video_id": "a", "new_title": "NEW A", "new_description": "desc a", "new_publish_at": "2026-12-01T22:30:00Z"},
        {"video_id": "b", "new_title": "NEW B", "new_description": "desc b", "new_publish_at": ""},
    ]
    changed = rt.sync_rows(rows, items, ok_ids={"a"}, unscheduled_ids={"t"})
    youtube = {row["platform_overrides"]["youtube"]["video_id"]: row for row in rows}
    assert changed == 2
    assert youtube["a"]["platform_overrides"]["youtube"]["title"] == "NEW A"
    assert youtube["a"]["platform_overrides"]["youtube"]["scheduled_time"] == "2026-12-01T22:30:00Z"
    assert youtube["a"]["base_metadata"]["title"] == "NEW A"
    assert youtube["b"]["platform_overrides"]["youtube"]["title"] == "old"
    assert youtube["t"]["platform_overrides"]["youtube"]["scheduled_time"] == ""


def test_tool_imports_the_scratch_path_helpers_it_uses() -> None:
    assert callable(rt.temp_path) and callable(rt.review_path)


def test_verification_flags_drift_and_unscheduled_leftovers() -> None:
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    item = {
        "video_id": "a",
        "new_title": "T",
        "new_description": "D",
        "privacy": "private",
        "new_publish_at": "2026-12-01T22:30:00Z",
    }
    good = {"id": "a", "title": "T", "description": "D\n", "privacy": "private", "publish_at": "2026-12-01T22:30:00Z"}
    held = {"id": "t", "title": "x", "description": "", "privacy": "private", "publish_at": ""}
    assert rt.verification([item], [{"video_id": "t"}], {"a": good, "t": held}, now=now)["ok"]
    bad = {**good, "title": "other", "publish_at": "2026-12-01T15:00:00Z"}
    report = rt.verification([item], [{"video_id": "t"}], {"a": bad, "t": held}, now=now)
    assert not report["ok"] and report["scheduled_at_12_00"] == 1
    still = {**held, "publish_at": "2026-11-05T22:30:00Z"}
    assert not rt.verification([item], [{"video_id": "t"}], {"a": good, "t": still}, now=now)["ok"]
