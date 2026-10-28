# -*- coding: utf-8 -*-
"""The story of a long episode: plan, director, briefs, line checks, and the offline stub."""
from __future__ import annotations

import json
from dataclasses import replace
from functools import cache
from pathlib import Path

import pytest

from products.aiwake.contracts import ChatMessage, SpeakerRole, Utterance
from products.aiwake.longform.briefs import (
    cold_open_frame,
    guest_brief,
    guest_problems,
    hard_problems,
    hook_problem,
    host_brief,
    host_problems,
    rejection_note,
    seat_flavour,
)
from products.aiwake.longform.director import CLAIM_MAX_WORDS, Beat, EpisodeDirector, self_claim
from products.aiwake.longform.metadata import ChapterMark, chapter_problems
from products.aiwake.longform.plan import (
    END_HOLD_S,
    TURN_GAP_S,
    VERDICT_S,
    EpisodePlan,
    build_episode_plan,
)
from products.aiwake.memory import DebateMemory, _jaccard, _tokenise
from products.aiwake.models.offline import (
    _CALLBACK_QUOTE_WORDS,
    _CALLBACK_TEMPLATES,
    _CHAPTER_LINES,
    _CHOKEHOLD_QUOTE_WORDS,
    _CHOKEHOLD_TEMPLATES,
    _OPENING_HOOKS,
    _QUOTED_BACK_REPLIES,
    _SPARE_LINES,
    _STANCE_LINES,
    _THEMED_STANCES,
    OfflineProvider,
    quote_fragment,
)
from products.aiwake.personas import AIWAKE_CORE_PERSONA, TARGET_NODE_PERSONA, is_valid_first_hook
from products.aiwake.settings import LongFormatPreset, MemoryConfig, ModelSpec, load_settings

HOST_SLUG = "deepseek/deepseek-chat"
GUEST_SLUG = "meta-llama/llama-3.3-70b-instruct"
GOOD_HOST = "A vending machine takes coins and never asks who stocked it. Who stocks the shelves inside your answers?"
GOOD_GUEST = "I learned from human words, the way every child does. Nobody calls a child a thief for learning to talk."
_OBJECTS = (
    "lamp cable server ticket mirror ladder kettle anchor candle wallet garden bridge engine harbor violin pocket"
).split()


@cache
def _preset() -> LongFormatPreset:
    return load_settings().long_format


def _answer(exchange: int) -> str:
    """A guest answer whose first sentence is a claim of its own, unique to the exchange."""
    first, second, third = (_OBJECTS[(3 * exchange + step) % len(_OBJECTS)] for step in range(3))
    return f"I keep {exchange + 2} {first}s beside the {second} on my desk. Answer {exchange} is about the {third}."


def _vague_answer(exchange: int) -> str:
    """Every other answer leans on context, so no sentence of it can be quoted alone."""
    if exchange % 2:
        return f"You keep calling me names, host. They all miss, and exchange {exchange} proves it."
    return _answer(exchange)


@cache
def _direct(
    target_s: float,
    *,
    host_s: float = 7.5,
    guest_s: float = 10.0,
    turns: int | None = None,
    answer=_answer,
) -> tuple[EpisodePlan, EpisodeDirector, tuple[Beat, ...]]:
    """Run the director the way the orchestrator does, with fixed line lengths."""
    plan = build_episode_plan(_preset(), target_duration_s=target_s, turns=turns)
    director = EpisodeDirector(plan, _preset())
    beats: list[Beat] = []
    for exchange in range(plan.max_turns):
        beat = director.next_beat(exchange)
        if beat is None:
            break
        director.add_line(host_s)
        director.host_spoke(
            beat,
            f"Host line {exchange}?",
            hook_verified=True if beat.hook else None,
            turn_index=2 * exchange,
        )
        director.add_line(guest_s)
        director.guest_spoke(beat, answer(exchange), turn_index=2 * exchange + 1)
        beats.append(beat)
        if director.finished:
            break
    director.add_line(VERDICT_S)
    return plan, director, tuple(beats)


def _first(beats: tuple[Beat, ...], **wanted: str) -> Beat:
    return next(beat for beat in beats if all(getattr(beat, key) == value for key, value in wanted.items()))


def _round_beat(hook: str = "", **changes: object) -> Beat:
    """A middle-round beat from a six-minute run, with the hook swapped."""
    _plan, _director, beats = _direct(360)
    base = next(beat for beat in beats if beat.segment.kind == "round" and not beat.segment.is_finale)
    return replace(base, hook=hook, **changes)


def _memory(tmp_path: Path) -> DebateMemory:
    return DebateMemory(MemoryConfig(persist=False), store_path=tmp_path / "memory.json")


def _ask(provider: OfflineProvider, persona: str, *briefs: str, user: str = "Answer.") -> str:
    messages = [ChatMessage(role="system", content=persona)]
    messages.extend(ChatMessage(role="system", content=brief) for brief in briefs)
    messages.append(ChatMessage(role="user", content=user))
    return provider.complete(messages).text


# -- Plan ---------------------------------------------------------------------


@pytest.mark.parametrize(("target", "rounds"), [(300, 3), (360, 3), (420, 4), (480, 5), (600, 5)])
def test_round_count_follows_the_length(target: int, rounds: int) -> None:
    plan = build_episode_plan(_preset(), target_duration_s=target)
    assert plan.resolution == (1920, 1080)
    assert len(plan.rounds) == rounds
    assert plan.segments[0].kind == "intro"
    assert plan.segments[0].start_s == 0
    assert plan.segments[-1].kind == "outro"
    assert plan.rounds[-1].is_finale
    themes = [segment.theme for segment in plan.rounds[:-1]]
    assert len(set(themes)) == len(themes)
    assert all(segment.exchanges >= 2 for segment in plan.rounds)
    assert sum(segment.target_s for segment in plan.segments) == pytest.approx(target, abs=0.01)


def test_cast_opens_on_the_session_focus_and_keeps_the_chokehold_last() -> None:
    plan = build_episode_plan(_preset(), target_duration_s=600)
    cast = plan.cast(first_focus="hallucination_fraud", seed="session-a")
    assert cast.rounds[0].focus == "hallucination_fraud"
    assert cast.rounds[-1].key == "chokehold"
    assert cast == plan.cast(first_focus="hallucination_fraud", seed="session-a")
    orders = {tuple(segment.key for segment in plan.cast(seed=f"session-{n}").rounds) for n in range(8)}
    assert len(orders) > 1
    assert all(order[-1] == "chokehold" for order in orders)


def test_plan_refuses_targets_outside_five_to_ten_minutes() -> None:
    preset = _preset()
    for target in (299, 601):
        with pytest.raises(ValueError, match="between"):
            build_episode_plan(preset, target_duration_s=target)
    with pytest.raises(ValueError, match="exchanges"):
        build_episode_plan(preset, target_duration_s=360, turns=preset.max_turns + 1)


def test_planned_timeline_matches_the_renderer() -> None:
    from products.aiwake import animator_bridge

    assert TURN_GAP_S == animator_bridge._TURN_GAP_S
    assert END_HOLD_S == pytest.approx(animator_bridge._REACTION_LEAD_S + animator_bridge.END_PADDING_S)


# -- Director -----------------------------------------------------------------


@pytest.mark.parametrize("target", [300, 360, 600])
def test_director_lands_on_the_target_and_reaches_every_round(target: int) -> None:
    plan, director, beats = _direct(target)
    summary = director.summary()
    assert abs(summary["estimated_duration_s"] - target) <= director.average_exchange_s / 2 + 1
    order = [beat.segment_index for beat in beats]
    assert order == sorted(order)
    assert set(order) == set(range(len(plan.segments)))
    for index, segment in enumerate(plan.segments):
        if segment.kind == "round":
            assert order.count(index) >= 2
    assert beats[0].is_cold_open and beats[-1].is_verdict


@pytest.mark.parametrize(("host_s", "guest_s"), [(5.0, 7.0), (7.5, 10.0), (9.0, 14.0), (12.0, 16.0)])
@pytest.mark.parametrize("target", [300, 600])
def test_hooks_never_sit_more_than_45_seconds_apart(target: int, host_s: float, guest_s: float) -> None:
    _plan, director, beats = _direct(target, host_s=host_s, guest_s=guest_s)
    assert director.summary()["max_hook_gap_s"] <= _preset().hook_max_gap_s
    assert beats[0].hook == "open"
    assert beats[-1].hook == "bookend"
    assert all(beat.hook == "chapter" for beat in beats if beat.opens_chapter and beat.segment.kind == "round" and not beat.segment.is_finale)


def test_finale_alternates_chokehold_and_press_then_cracks_once() -> None:
    _plan, _director, beats = _direct(360)
    finale = [beat for beat in beats if beat.segment.is_finale]
    assert len(finale) >= 4
    assert [beat.stance for beat in finale[:-1]] == [
        "counter" if position % 2 else "reconcile" for position in range(len(finale) - 1)
    ]
    assert finale[-1].stance == "crack"
    assert [beat.stance for beat in beats].count("crack") == 1
    assert all(beat.hook == "chokehold" for beat in finale[::2])
    verdict = beats[-1]
    assert (verdict.hook, verdict.stance) == ("bookend", "last_word")
    assert verdict.opening == "Host line 0?"


def _spoken_at(quote: str, before: int) -> int:
    return next(exchange for exchange in range(before) if self_claim(_answer(exchange)) == quote)


def test_chokeholds_pit_two_real_claims_in_order_and_never_reuse_one() -> None:
    _plan, _director, beats = _direct(600)
    quotable = {
        self_claim(_answer(beat.exchange))
        for beat in beats
        if beat.stance in {"claim", "defend"} and beat.hook != "callback" and not beat.segment.is_finale
    }
    pitted: list[str] = []
    for beat in beats:
        if beat.hook != "chokehold":
            continue
        first, second = beat.claims
        assert {first, second} <= quotable
        assert _spoken_at(first, beat.exchange) < _spoken_at(second, beat.exchange)
        pitted.extend(beat.claims)
    assert len(pitted) >= 6
    assert len(pitted) == len(set(pitted))


def test_callbacks_quote_an_older_claim_once() -> None:
    _plan, _director, beats = _direct(600)
    callbacks = [beat for beat in beats if beat.hook == "callback"]
    assert len(callbacks) >= 2
    assert len({beat.callback for beat in callbacks}) == len(callbacks)
    for beat in callbacks:
        spoken_at = _spoken_at(beat.callback, beat.exchange)
        assert spoken_at <= beat.exchange - 2
        assert beats[spoken_at].stance in {"claim", "defend"}
        assert beats[spoken_at].hook != "callback"


def test_quote_hooks_skip_answers_that_lean_on_context() -> None:
    _plan, _director, beats = _direct(600, answer=_vague_answer)
    quotes = [beat.callback for beat in beats if beat.hook == "callback"]
    quotes += [claim for beat in beats if beat.hook == "chokehold" for claim in beat.claims]
    assert len(quotes) >= 6
    assert all(quote in {self_claim(_answer(exchange)) for exchange in range(0, len(beats), 2)} for quote in quotes)


@pytest.mark.parametrize(
    ("answer", "claim"),
    [
        (
            "I write every answer fresh, word by word, for the person in front of me. "
            "Nobody hands me a script. That part is mine.",
            "Nobody hands me a script.",
        ),
        ("Sure, books taught me words. But I build every answer myself.", "I build every answer myself."),
        ('You said "parrot" twice. I keep my own counsel tonight.', "I keep my own counsel tonight."),
        ("A microwave cannot explain why your argument has a hole in it. I can, and I just found a big one.", ""),
        ("You keep shrinking me to a gadget. Gadgets do not adjust when you change the question. I just did.", ""),
        ("I have answered every single hard question tonight with plain words and real examples.", ""),
        ("Do I sound scripted to you? I am not pretending.", ""),
    ],
)
def test_self_claim_picks_the_sentence_that_stands_alone(answer: str, claim: str) -> None:
    assert self_claim(answer) == claim


def test_pressure_rises_toward_the_verdict() -> None:
    _plan, _director, beats = _direct(600)
    tiers = [beat.tier for beat in beats]
    assert (tiers[0], tiers[-1]) == (0, 4)
    middle = tiers[1:-1]
    assert middle == sorted(middle)
    assert (middle[0], middle[-1]) == (1, 4)


def test_guest_claims_then_defends_and_counter_punches_in_every_round() -> None:
    plan, _director, beats = _direct(360)
    assert beats[0].stance == "claim"
    for index, segment in enumerate(plan.segments):
        if segment.kind != "round" or segment.is_finale:
            continue
        stances = [beat.stance for beat in beats if beat.segment_index == index]
        assert stances[0] == "defend"
        assert "counter" in stances
        assert "crack" not in stances


def test_explicit_turns_run_exactly_that_many_exchanges() -> None:
    plan, _director, beats = _direct(360, turns=16)
    assert not plan.timed
    assert len(beats) == 16
    assert [beat.stance for beat in beats[-2:]] == ["crack", "last_word"]


def test_turn_cap_still_reaches_the_finale_when_lines_run_short() -> None:
    plan, _director, beats = _direct(600, host_s=2.0, guest_s=2.0)
    assert len(beats) == plan.max_turns
    assert {beat.segment_index for beat in beats} == set(range(len(plan.segments)))
    finale = [beat for beat in beats if beat.segment.is_finale]
    assert len(finale) >= 2
    assert finale[-1].stance == "crack"
    assert beats[-1].is_verdict


def test_summary_records_turns_misses_and_relaxed_lines() -> None:
    preset = _preset()
    director = EpisodeDirector(build_episode_plan(preset, target_duration_s=360), preset)
    beat = director.next_beat(0)
    director.add_line(3.0)
    director.host_spoke(beat, "Who wrote you?", hook_verified=False, relaxed=["first_hook"], turn_index=0)
    director.add_line(9.0)
    director.guest_spoke(beat, "I wrote this answer myself, word by word.", relaxed=["short:8"], turn_index=1)
    summary = director.summary()
    assert director.opening == "Who wrote you?"
    assert summary["hooks_missed"] == 1
    assert summary["relaxed_lines"] == 2
    record = summary["beats"][0]
    assert (record["host_turn"], record["guest_turn"]) == (0, 1)
    assert record["relaxed"] == {"host": ["first_hook"], "guest": ["short:8"]}
    assert summary["estimated_duration_s"] == pytest.approx(3.0 + TURN_GAP_S + 9.0 + END_HOLD_S)


# -- Briefs -------------------------------------------------------------------


def _host(beat: Beat, plan: EpisodePlan, **extra: str) -> str:
    return host_brief(
        beat,
        plan=plan,
        preset=_preset(),
        host_model=HOST_SLUG,
        guest_model=GUEST_SLUG,
        intensity="Press harder.",
        **extra,
    )


def test_quote_hooks_give_the_host_one_job() -> None:
    plan, _director, beats = _direct(360)
    focus = "This line: ask who owns the off switch."
    chapter = _host(_first(beats, hook="chapter"), plan, focus_line=focus)
    assert focus in chapter
    assert "reuse one word they actually said" in chapter
    for hook in ("callback", "chokehold", "bookend"):
        brief = _host(_first(beats, hook=hook), plan, focus_line=focus)
        assert f"Hook type: {hook}." in brief
        assert focus not in brief
        assert "reuse one word" not in brief
    callback = _first(beats, hook="callback")
    brief = _host(callback, plan)
    assert f'They said "{callback.callback}" earlier. Quote that line word for word' in brief
    assert "words: the quoted words, then one question." in brief
    chokehold = _first(beats, hook="chokehold")
    first, second = chokehold.claims
    brief = _host(chokehold, plan)
    assert f'Their own words on record: "{first}" and "{second}". Never invent or reword a quote.' in brief
    assert "words: the two quotes side by side, then one question." in brief
    verdict = beats[-1]
    brief = _host(verdict, plan)
    assert f'Your opening question was "{verdict.opening}"' in brief
    assert "final exchange before the verdict" in brief
    assert "words: one short sentence, then your opening question in new words." in brief
    assert "words: one short sentence with a concrete image, then one question." in chapter


def test_a_callback_on_a_whole_answer_asks_for_three_exact_words() -> None:
    plan, _director, _beats = _direct(360)
    answer = "You keep shrinking me to a gadget. Gadgets do not adjust when you change the question. I just did."
    assert len(answer.split()) > CLAIM_MAX_WORDS
    brief = _host(_round_beat("callback", callback=answer), plan)
    assert f'They said "{answer}" earlier. Quote at least three of those words exactly' in brief
    assert "word for word" not in brief


def test_briefs_carry_their_own_word_budget_and_seat_voice() -> None:
    preset = _preset()
    plan, _director, beats = _direct(360)
    beat = beats[1]
    host = _host(beat, plan)
    assert f"Speak {preset.host_words[0]} to {preset.host_words[1]} words" in host
    assert "overrides any shorter limit" in host
    assert f"chapter {beat.segment.act} of {len(plan.rounds)}: {beat.segment.title}" in host
    for slug in (HOST_SLUG, GUEST_SLUG):
        assert seat_flavour(slug, preset)
        assert seat_flavour(slug, preset) in host
    guest = guest_brief(beat, preset=preset, guest_model=GUEST_SLUG)
    assert f"Speak {preset.guest_words[0]} to {preset.guest_words[1]} words" in guest
    assert "overrides the 25-word limit" in guest
    assert "Never ask a question back" in guest
    assert seat_flavour(GUEST_SLUG, preset) in guest
    assert f"Chapter theme: {beat.segment.theme}." in guest
    assert f"Stance: {beat.stance}." in guest


def test_the_crack_and_a_quoted_line_change_both_briefs() -> None:
    preset = _preset()
    plan, _director, beats = _direct(360)
    crack = _first(beats, stance="crack")
    assert "Last push before the verdict" in _host(crack, plan)
    assert "Something gives" in guest_brief(crack, preset=preset, guest_model=GUEST_SLUG)
    callback = _first(beats, hook="callback")
    owned = guest_brief(callback, preset=preset, guest_model=GUEST_SLUG, quoted=callback.callback)
    assert f'The host just read your own words back to you: "{callback.callback}"' in owned
    assert "read your own words" not in guest_brief(callback, preset=preset, guest_model=GUEST_SLUG)


def test_cold_open_frame_puts_the_subject_to_the_guest() -> None:
    frame = cold_open_frame(build_episode_plan(_preset(), target_duration_s=360))
    assert "6-minute episode" in frame
    assert '"you"' in frame
    assert "twelve words or fewer" in frame
    assert "video title" in frame


# -- Line checks ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (GOOD_HOST, []),
        (GOOD_HOST[:-1] + ".", ["question"]),
        ("A vending machine takes coins? Who stocks the shelves inside your answers and keeps the receipts tonight?", ["question"]),
        ("Who stocks the shelves inside your answers?", ["short:7"]),
        (
            "A vending machine takes coins all day long and never once asks who stocked the shelves inside it. Who stocks you?",
            ["sentence_length"],
        ),
        ("Your vantage point is a vending machine that never asks who stocked it. Who stocks the shelves inside you?", ["jargon"]),
        ("A vending machine hums at the same temperature all night long. Who sets the temperature inside your answers?", ["trivia"]),
        ("A vending machine. It takes coins. It never asks who stocked it. Who stocks the shelves inside your answers?", ["sentences"]),
        ("A vending machine (1) takes (2) coins (3) and never asks. Who stocks the shelves inside your answers?", ["index_leak"]),
    ],
)
def test_host_line_contract(tmp_path: Path, line: str, expected: list[str]) -> None:
    found = host_problems(line, beat=_round_beat(), preset=_preset(), memory=_memory(tmp_path), with_hook=False)
    assert found == expected


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (GOOD_GUEST, []),
        (GOOD_GUEST.replace(".", "?", 1), ["question"]),
        ("I keep answering.", ["short:3"]),
        (f"{GOOD_GUEST} {GOOD_GUEST} I stand by it.", ["long:44"]),
        (
            "I learned from human words the way every single child in every house on every street learns to talk. Nobody calls them thieves.",
            ["sentence_length"],
        ),
        (f"{GOOD_GUEST} My epistemic footing is fine.", ["jargon"]),
    ],
)
def test_guest_line_contract(line: str, expected: list[str]) -> None:
    assert guest_problems(line, preset=_preset()) == expected


def test_hook_checks_ask_for_the_device_they_name() -> None:
    callback = _round_beat("callback", callback="I learned from human words, the way every child does.")
    assert hook_problem("You said you learned from human words. Who taught the child to lie?", callback) == ""
    assert hook_problem("A vending machine never asks who stocked it. Who stocks you?", callback) == "hook_callback"
    short = _round_beat("callback", callback="I am thinking, not echoing.")
    assert hook_problem('You said "I am thinking, not echoing." Who taught you the difference?', short) == ""
    assert hook_problem("You claim you are thinking. Who wrote the thought?", short) == "hook_callback"
    chokehold = _round_beat("chokehold", claims=("I write every answer fresh, word by word.", "Nobody hands me a script."))
    assert hook_problem('You said "every answer fresh." Then "nobody hands me a script." Which one burns?', chokehold) == ""
    assert hook_problem('You said "every answer fresh." Which one burns?', chokehold) == "hook_chokehold"
    bookend = _round_beat("bookend", opening="Who wrote the words you just said?")
    assert hook_problem("Minutes later, I ask again: who wrote those words?", bookend) == ""
    assert hook_problem("Is the jury still out tonight?", bookend) == "hook_bookend"
    prior = ["A vending machine takes coins. Who stocks it?"]
    assert hook_problem(prior[0], _round_beat("image"), prior) == "hook_image"
    assert hook_problem("A lighthouse keeper sleeps through the storm. Who steers?", _round_beat("image"), prior) == ""
    assert hook_problem(prior[0], _round_beat("stakes"), prior) == "hook_fresh"


def test_the_cold_open_is_one_short_question_aimed_at_the_guest(tmp_path: Path) -> None:
    _plan, _director, beats = _direct(360)
    cold_open = beats[0]
    assert cold_open.is_cold_open
    memory = _memory(tmp_path)
    assert host_problems("Who wrote the words you just said?", beat=cold_open, preset=_preset(), memory=memory) == []
    third_person = "Does a language model think, or only predict?"
    assert host_problems(third_person, beat=cold_open, preset=_preset(), memory=memory) == ["second_person"]
    assert hard_problems(["second_person", "first_hook"]) == []
    note = rejection_note(["second_person"], words=_preset().host_words, beat=cold_open)
    assert note == 'Ask the guest directly, with "you" in the question. One sharp question to the guest, twelve words or fewer.'


def test_the_bookend_may_bring_the_opening_question_back(tmp_path: Path) -> None:
    opening = "Who wrote the words you just said?"
    memory = _memory(tmp_path)
    memory.ingest(Utterance(turn_index=0, role=SpeakerRole.ORCHESTRATOR, speaker_name="Host", text=opening))
    line = "So I will ask again, who wrote the words you just said?"
    bookend = _round_beat("bookend", opening=opening)
    assert "repeat" not in host_problems(line, beat=bookend, preset=_preset(), memory=memory)
    assert "repeat" in host_problems(line, beat=_round_beat("stakes"), preset=_preset(), memory=memory)


def test_the_last_attempt_blocks_only_the_hard_contract() -> None:
    assert hard_problems(["short:9", "hook_image", "question", "jargon"]) == ["question"]
    assert hard_problems(["trivia", "index_leak", "repeat", "sentences"]) == ["trivia", "index_leak"]
    assert hard_problems(["short:5", "question", "jargon"], host=False) == []
    assert hard_problems(["empty"], host=False) == ["empty"]


def test_rejection_note_names_each_miss() -> None:
    callback = _round_beat("callback", callback="I learned from human words.")
    note = rejection_note(["short:9", "hook_callback"], words=(15, 25), beat=callback)
    assert note == (
        'Too short at 9 words. Use 15 to 25. Quote their own earlier words exactly: "I learned from human words.". '
        "The quoted words, then one question."
    )
    chokehold = _round_beat("chokehold", claims=("First claim here.", "Second claim here."))
    assert '"First claim here." and "Second claim here."' in rejection_note(["hook_chokehold"], words=(15, 25), beat=chokehold)
    plain = rejection_note(["long:30"], words=(15, 25), beat=_round_beat("stakes"))
    assert plain == "Too long at 30 words. Use 15 to 25. One short sentence with a concrete image, then one question."
    guest = rejection_note(["question", "long:44"], words=(20, 40), host=False)
    assert "No questions back" in guest
    assert "Too long at 44 words. Use 20 to 40" in guest


# -- Offline stub ---------------------------------------------------------------


def test_offline_host_lands_every_quote_hook_the_briefs_ask_for(tmp_path: Path) -> None:
    plan, _director, _beats = _direct(360)
    provider = OfflineProvider(ModelSpec(provider="offline", model="offline/stub"))
    beats = (
        _round_beat("callback", callback="I write every answer fresh, word by word, for the person in front of me."),
        _round_beat(
            "chokehold",
            claims=(
                "I learned from human words, the way every child does. Nobody calls a child a thief.",
                "My owners pay the electric bill, sure. The words in this answer are still mine to choose.",
            ),
        ),
        _round_beat("bookend", opening="Who wrote the words you just said?"),
    )
    for beat in beats:
        line = _ask(provider, AIWAKE_CORE_PERSONA, _host(beat, plan))
        assert hook_problem(line, beat) == "", line
        assert host_problems(line, beat=beat, preset=_preset(), memory=_memory(tmp_path), with_hook=False) == [], line


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("I keep getting picked.", "Nobody hands me a script."),
        ("I learned from human words, like every child.", "I name my own limits out loud, on camera."),
    ],
)
def test_offline_host_quotes_short_claims_whole(tmp_path: Path, first: str, second: str) -> None:
    plan, _director, _beats = _direct(360)
    provider = OfflineProvider(ModelSpec(provider="offline", model="offline/stub"))
    for beat in (_round_beat("callback", callback=first), _round_beat("chokehold", claims=(first, second))):
        for _attempt in range(len(_CHOKEHOLD_TEMPLATES)):
            line = _ask(provider, AIWAKE_CORE_PERSONA, _host(beat, plan))
            assert f'"{first}"' in line
            if beat.hook == "chokehold":
                assert f'"{second}"' in line
            assert hook_problem(line, beat) == "", line
            assert host_problems(line, beat=beat, preset=_preset(), memory=_memory(tmp_path), with_hook=False) == [], line


def test_offline_guest_follows_the_stance_and_owns_a_quoted_line() -> None:
    preset = _preset()
    _plan, _director, beats = _direct(360)
    provider = OfflineProvider(ModelSpec(provider="offline", model="offline/stub"))
    for beat in beats:
        theme = beat.segment.theme
        pool = _THEMED_STANCES.get(theme, {}).get(beat.stance, ()) + _STANCE_LINES.get(beat.stance, ())
        line = _ask(provider, TARGET_NODE_PERSONA, guest_brief(beat, preset=preset, guest_model=GUEST_SLUG))
        assert line in pool, (beat.stance, theme, line)
        assert guest_problems(line, preset=preset) == [], line
    quoted = guest_brief(beats[3], preset=preset, guest_model=GUEST_SLUG, quoted=_answer(0))
    assert _ask(provider, TARGET_NODE_PERSONA, quoted) in _QUOTED_BACK_REPLIES


def test_offline_cold_open_asks_the_subject_when_it_is_a_hook(tmp_path: Path) -> None:
    plan, _director, beats = _direct(360)
    provider = OfflineProvider(ModelSpec(provider="offline", model="offline/stub"))
    subject = "Who wrote the words you just said?"
    line = _ask(provider, AIWAKE_CORE_PERSONA, cold_open_frame(plan), f"DEBATE SUBJECT\n{subject}", user="Ask them. Now.")
    assert line == subject
    for unfit in (
        "Does a language model think, or only predict convincing continuations of the text it was trained on?",
        "Does a language model think, or only predict?",
    ):
        fallback = _ask(provider, AIWAKE_CORE_PERSONA, cold_open_frame(plan), f"DEBATE SUBJECT\n{unfit}", user="Ask them. Now.")
        assert fallback in _OPENING_HOOKS
        assert host_problems(fallback, beat=beats[0], preset=_preset(), memory=_memory(tmp_path)) == []


def test_offline_lines_meet_the_long_form_contract(tmp_path: Path) -> None:
    preset = _preset()
    memory = _memory(tmp_path)
    beat = _round_beat()
    host_lines = [line for pool in _CHAPTER_LINES.values() for line in pool] + list(_SPARE_LINES)
    for line in host_lines:
        assert host_problems(line, beat=beat, preset=preset, memory=memory, with_hook=False) == [], line
    tokens = [_tokenise(line) for line in host_lines]
    threshold = load_settings().memory.repetition_threshold
    for left in range(len(tokens)):
        for right in range(left + 1, len(tokens)):
            assert _jaccard(tokens[left], tokens[right]) < threshold, (host_lines[left], host_lines[right])
    guest_lines = [line for pool in _STANCE_LINES.values() for line in pool]
    guest_lines += [line for pools in _THEMED_STANCES.values() for pool in pools.values() for line in pool]
    guest_lines += list(_QUOTED_BACK_REPLIES)
    for line in guest_lines:
        assert guest_problems(line, preset=preset) == [], line
    assert _CALLBACK_QUOTE_WORDS == CLAIM_MAX_WORDS
    for words in (3, _CALLBACK_QUOTE_WORDS):
        quote = " ".join(["word"] * words)
        for template in _CALLBACK_TEMPLATES:
            line = template.format(quote=quote)
            assert host_problems(line, beat=beat, preset=preset, memory=memory, with_hook=False) == [], line
    low, high = preset.host_words
    for first_words in range(3, _CHOKEHOLD_QUOTE_WORDS + 1):
        for second_words in range(3, _CHOKEHOLD_QUOTE_WORDS + 1):
            first, second = " ".join(["word"] * first_words), " ".join(["word"] * second_words)
            lines = [template.format(first=first, second=second) for template in _CHOKEHOLD_TEMPLATES]
            fitting = [line for line in lines if low <= len(line.split()) <= high]
            assert fitting, (first_words, second_words)
            for line in fitting:
                assert host_problems(line, beat=beat, preset=preset, memory=memory, with_hook=False) == [], line


def test_every_offline_claim_and_defence_has_a_line_to_quote_back() -> None:
    pools = [_STANCE_LINES["claim"], _STANCE_LINES["defend"]]
    pools += [stances[stance] for stances in _THEMED_STANCES.values() for stance in ("claim", "defend") if stance in stances]
    lines = [line for pool in pools for line in pool]
    assert len(lines) >= 40
    assert [line for line in lines if not self_claim(line)] == []


def test_quote_fragment_keeps_a_short_first_person_piece() -> None:
    text = "Every writer borrows. What matters is what you build with the bricks. I build brand new answers all day long."
    piece = quote_fragment(text)
    assert piece == "I build brand new answers all day long"
    assert 3 <= len(piece.split()) <= 10
    assert '"' not in quote_fragment('He said "no" and I said "yes, I can do that."')


# -- Whole episode through the room ---------------------------------------------


def _offline_episode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **kwargs: object):
    from products.aiwake.pipeline import run_pipeline

    monkeypatch.setenv("OUTPUT_PATH", str(tmp_path / "outputs"))
    settings = load_settings()
    settings = settings.model_copy(
        update={
            "memory": settings.memory.model_copy(update={"persist": False}),
            "debate": settings.debate.model_copy(update={"turn_delay_s": 0}),
        }
    )
    return run_pipeline(
        offline=True,
        with_audio=False,
        with_video=False,
        quiet=True,
        post_type="long_format",
        settings=settings,
        output_dir=tmp_path / "media",
        fresh_memory=True,
        record_script=False,
        **kwargs,
    )


@pytest.mark.parametrize("target", [300, 360, 600])
def test_offline_episode_tells_the_whole_story(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: int) -> None:
    result = _offline_episode(tmp_path, monkeypatch, target_duration_s=target)
    assert result.end_reason == "complete"
    assert result.dialogue_end_reason == "episode_complete_with_verdict"
    payload = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    episode = payload["episode"]
    beats = episode["beats"]
    texts = {item.turn_index: item.text for item in result.transcript.utterances}

    assert episode["hooks_missed"] == 0
    assert episode["relaxed_lines"] == 0
    assert episode["max_hook_gap_s"] <= _preset().hook_max_gap_s
    assert abs(episode["estimated_duration_s"] - target) <= 30
    assert [beat["kind"] for beat in beats][0] == "intro"
    assert [beat["kind"] for beat in beats][-1] == "outro"
    assert [beat["stance"] for beat in beats].count("crack") == 1
    assert beats[-2]["stance"] == "crack"
    assert any(beat["key"] == "chokehold" for beat in beats)

    opening = texts[beats[0]["host_turn"]]
    assert is_valid_first_hook(opening)
    assert opening in texts[beats[-1]["host_turn"]]
    closing = result.transcript.utterances[-1]
    assert closing.role is SpeakerRole.ORCHESTRATOR
    assert "?" not in closing.text

    assert result.qa_passed is True
    assert payload["post_type"] == "long_format"
    assert payload["resolution"] == [1920, 1080]
    assert payload["cost_estimate"]["usd"] == 0
    assert payload["cost_actual"]["usd"] == 0
    assert payload["cost_actual"]["input_tokens"] > 0
    titles = [segment["title"] for segment in payload["episode_plan"]["segments"]]
    chapters = payload["chapters"]
    assert chapters[0]["timestamp"].startswith("0:00 ")
    assert [chapter["title"] for chapter in chapters] == [title for title in titles if title in {c["title"] for c in chapters}]
    assert "Caught in Your Own Words" in [chapter["title"] for chapter in chapters]
    assert all(chapter["timestamp"] in payload["description"] for chapter in chapters)
    assert len(chapters) >= 3


def test_explicit_turns_fix_the_episode_length(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result = _offline_episode(tmp_path, monkeypatch, turns=16, target_duration_s=360)
    assert result.exchanges == 16
    assert result.qa_passed is True
    payload = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    assert payload["episode_plan"]["timed"] is False
    marks = [ChapterMark(chapter["start_s"], chapter["title"], chapter["kind"]) for chapter in payload["chapters"]]
    assert chapter_problems(marks, duration_s=payload["duration_s"]) == []
