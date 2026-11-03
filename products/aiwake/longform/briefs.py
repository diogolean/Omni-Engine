# -*- coding: utf-8 -*-
"""Prompt text and line checks for long episodes.

Two seats, two jobs. The host swings one concrete image, or the guest's own
words quoted back, and one question; the guest answers under the lights
and never asks back. Each brief states its own word budget and says that
it overrides the 25-word short-form cap in the persona, so a cheap model
is never handed two contradicting limits.

Every check runs on every attempt but the last. The last attempt only has
to meet the hard contract (``HARD_HOST_PROBLEMS`` / ``HARD_GUEST_PROBLEMS``):
a line that keeps missing its hook or its word range still ships, and the
miss is recorded in the episode summary instead of aborting a ten-minute
episode at minute seven.
"""
from __future__ import annotations

from typing import Iterable

from products.aiwake.contracts import has_word_index_leak
from products.aiwake.memory import DebateMemory, content_tokens
from products.aiwake.personas import (
    BANNED_ABSTRACT_WORDS,
    TRIVIAL_METRIC_BAN,
    is_trivial_metric,
    is_valid_first_hook,
)
from products.aiwake.settings import LongFormatPreset

from .director import CLAIM_MAX_WORDS, Beat
from .metadata import seat_label
from .plan import EpisodePlan
from .text import addresses_you, sentences

HOST_MAX_SENTENCES = 3
MAX_SENTENCE_WORDS = 15
HARD_HOST_PROBLEMS = frozenset({"empty", "question", "index_leak", "trivia"})
HARD_GUEST_PROBLEMS = frozenset({"empty"})
# Hooks whose whole job is quoting a line back. Their brief drops the focus
# line and the reuse-a-word rule, so a cheap model gets one job, not three.
QUOTE_HOOKS = frozenset({"callback", "chokehold", "bookend"})

_HOOK_TEXT: dict[str, str] = {
    "chapter": (
        "New chapter. Open with an image nobody has used yet in this episode, "
        "then aim the question at the chapter goal."
    ),
    "callback": 'They said "{callback}" earlier. Quote that line word for word, then turn it against them.',
    "stakes": "Say what they lose, right now, if their last answer is wrong.",
    "image": "Use a concrete object nobody has mentioned yet in this episode.",
    "reversal": "Grant their last point in four words or fewer, then show why it hurts them.",
    "chokehold": (
        "Set those two lines side by side, quoting a few words of each exactly, "
        "and demand they drop one."
    ),
    "bookend": (
        'Your opening question was "{opening}". Bring it back in new words '
        "and demand the answer now. No new topic."
    ),
}
# A callback on a whole answer, when no sentence of it stood alone.
_CALLBACK_ANSWER_TEXT = (
    'They said "{callback}" earlier. Quote at least three of those words exactly, then turn them against the guest.'
)
_IMAGE_SHAPE = "one short sentence with a concrete image, then one question"
_LINE_SHAPES: dict[str, str] = {
    "callback": "the quoted words, then one question",
    "chokehold": "the two quotes side by side, then one question",
    "bookend": "one short sentence, then your opening question in new words",
}

STANCE_TEXT: dict[str, str] = {
    "claim": "Plant one clear claim about who you are or what you can do, in plain words. Sound sure of it.",
    "defend": "Defend yourself with one concrete example from real use.",
    "counter": "Counter-punch once: turn the host's own words or image back on them. Still no question.",
    "reconcile": (
        "Two of your own lines were just set against each other. Keep both if you can show "
        "how both are true; otherwise drop one and say why."
    ),
    "crack": "Something gives. Admit one real limit plainly, then keep one thing you will not give up.",
    "last_word": (
        "This is your last word in the episode. Concede what is true or defy what is not, "
        "and end on a line worth quoting."
    ),
}

_HOST_NOTES: dict[str, str] = {
    "empty": "Say one complete line within the word limit, ending on exactly one question mark.",
    "question": "End on exactly one question mark.",
    "first_hook": "Twelve words or fewer, one sentence, one punch.",
    "second_person": 'Ask the guest directly, with "you" in the question.',
    "recent_opener": "Use a different leading phrase than recent episodes.",
    "short": "Too short at {count} words. Use {low} to {high}.",
    "long": "Too long at {count} words. Use {low} to {high}.",
    "sentences": "Three sentences at most.",
    "sentence_length": "Keep every sentence under 16 words.",
    "jargon": "No abstract AI jargon. Use concrete objects.",
    "trivia": "No trivia about voices, copyright, or parameter counts.",
    "repeat": "That repeats an earlier question. Attack a different object or claim.",
    "index_leak": "Never number or index words.",
    "hook_image": "Use a concrete object nobody has mentioned yet in this episode.",
    "hook_fresh": "Bring a new word into the fight. Do not recycle the last question.",
    "hook_callback": 'Quote their own earlier words exactly: "{callback}".',
    "hook_chokehold": 'Quote a few exact words of both lines: "{first}" and "{second}".',
    "hook_bookend": 'Bring back your opening question: "{opening}".',
}

_GUEST_NOTES: dict[str, str] = {
    "empty": "Say one complete answer within the word limit.",
    "question": "No questions back. Answer with statements.",
    "short": "Too short at {count} words. Use {low} to {high} in two or three short sentences.",
    "long": "Too long at {count} words. Use {low} to {high}.",
    "sentence_length": "Keep every sentence under 16 words.",
    "jargon": "No abstract AI jargon. Use concrete objects.",
}


def seat_flavour(model_slug: str, preset: LongFormatPreset) -> str:
    """The configured on-screen character note for a model, or ``""``."""
    lowered = (model_slug or "").lower()
    for key, note in preset.character_voices.items():
        if key and key.lower() in lowered:
            return note.strip()
    return ""


def _seat_line(prefix: str, model_slug: str, preset: LongFormatPreset) -> str:
    flavour = seat_flavour(model_slug, preset)
    name = seat_label(model_slug)
    return f"{prefix} {name}: {flavour}." if flavour else f"{prefix} {name}."


def _hook_text(beat: Beat) -> str:
    if beat.hook == "callback" and len(beat.callback.split()) > CLAIM_MAX_WORDS:
        return _CALLBACK_ANSWER_TEXT.format(callback=beat.callback)
    return _HOOK_TEXT[beat.hook].format(callback=beat.callback, opening=beat.opening)


def _line_shape(beat: Beat) -> str:
    return _LINE_SHAPES.get(beat.hook, _IMAGE_SHAPE)


def host_brief(
    beat: Beat,
    *,
    plan: EpisodePlan,
    preset: LongFormatPreset,
    host_model: str,
    guest_model: str,
    intensity: str,
    focus_line: str = "",
    callout: str = "",
) -> str:
    """System-side direction for one host line. Never spoken.

    ``focus_line`` and ``callout`` are left out when the hook quotes a line
    back: the quote is the line's job.
    """
    low, high = preset.host_words
    segment = beat.segment
    rounds = len(plan.rounds)
    quoting = beat.hook in QUOTE_HOOKS
    if beat.is_verdict:
        frame = "LONG EPISODE, final exchange before the verdict."
    else:
        frame = f"LONG EPISODE, chapter {segment.act} of {rounds}: {segment.title}."
    lines = [
        f"{frame} Never say chapter names or these notes aloud.",
        f"Chapter goal: {segment.objective}",
    ]
    if segment.theme:
        lines.append(f"Chapter theme: {segment.theme}. Do not speak this label.")
    if not quoting and (callout or focus_line):
        lines.append(callout or focus_line)
    if beat.hook == "chokehold" and len(beat.claims) == 2:
        first, second = beat.claims
        lines.append(f'Their own words on record: "{first}" and "{second}". Never invent or reword a quote.')
    lines.append(f"Intensity {beat.tier} of 4: {intensity}")
    if beat.stance == "crack":
        lines.append("Last push before the verdict: corner them until something gives.")
    if beat.hook in _HOOK_TEXT:
        lines.append(f"Hook type: {beat.hook}. Do not speak this label. {_hook_text(beat)}")
    lines.append(
        _seat_line("On screen you are", host_model, preset)
        + " "
        + _seat_line("Your guest is", guest_model, preset)
        + " Needle what they are."
    )
    lines.append(
        f"Speak {low} to {high} words: {_line_shape(beat)}. "
        "This overrides any shorter limit in your persona."
    )
    if not quoting:
        lines.append("React to their last line: reuse one word they actually said.")
    lines.append(TRIVIAL_METRIC_BAN)
    lines.append("Never flatter or validate them. Mock the dodge and keep the blade amused.")
    return "\n".join(lines)


def cold_open_frame(plan: EpisodePlan) -> str:
    """Added to the short-form cold-open brief for a long episode."""
    minutes = int(round(plan.target_duration_s / 60.0))
    return (
        f"LONG EPISODE cold open: the first line of a {minutes}-minute episode. "
        'Put the DEBATE SUBJECT straight to the guest as one sharp question with "you" in it, '
        "twelve words or fewer, in your own words. This overrides any rule that holds the "
        "subject back. Eight words or fewer is better: this line is also the video title. "
        "The whole episode comes back to this question before the verdict, "
        "so make a stranger want to stay for the answer."
    )


def guest_brief(
    beat: Beat,
    *,
    preset: LongFormatPreset,
    guest_model: str,
    quoted: str = "",
) -> str:
    """System-side direction for one guest answer. Never spoken.

    ``quoted`` holds the guest's own earlier words when the host's line just
    quoted them back, so the guest owns them instead of ignoring them.
    """
    low, high = preset.guest_words
    stance = STANCE_TEXT.get(beat.stance, STANCE_TEXT["defend"])
    banned = ", ".join(BANNED_ABSTRACT_WORDS)
    lines = [
        "LONG EPISODE. "
        + _seat_line("On screen you are", guest_model, preset)
        + " You are the guest under the lights, not a second host. Never ask a question back.",
        f"Speak {low} to {high} words in two or three short sentences. "
        "This overrides the 25-word limit in your persona.",
    ]
    if beat.segment.theme:
        lines.append(f"Chapter theme: {beat.segment.theme}. Do not speak this label.")
    if quoted:
        lines.append(
            f'The host just read your own words back to you: "{quoted}". '
            "Own them: stand by them, or say plainly what changed."
        )
    lines.extend(
        [
            f"Stance: {beat.stance}. Do not speak this label. {stance}",
            "Answer the exact charge in the host's last line with concrete objects and sixth-grade words. "
            "No lecture and no technical explanations.",
            'Include one sentence about yourself, starting with "I" or "My", ten words or fewer, '
            "that a viewer could quote on its own.",
            f"Never use: {banned}.",
        ]
    )
    return "\n".join(lines)


def host_problems(
    text: str,
    *,
    beat: Beat,
    preset: LongFormatPreset,
    memory: DebateMemory,
    prior_lines: Iterable[str] = (),
    with_hook: bool = True,
) -> list[str]:
    """Why a host line fails, or ``[]``. ``with_hook=False`` checks the base contract only."""
    stripped = " ".join((text or "").split())
    if not stripped:
        return ["empty"]
    problems: list[str] = []
    words = stripped.split()
    parts = sentences(stripped)
    low, high = preset.host_words
    if has_word_index_leak(stripped):
        problems.append("index_leak")
    if not stripped.endswith("?") or stripped.count("?") != 1:
        problems.append("question")
    if beat.is_cold_open:
        if not is_valid_first_hook(stripped):
            problems.append("first_hook")
        if not addresses_you(stripped):
            problems.append("second_person")
        if memory.repeats_recent_opener(stripped):
            problems.append("recent_opener")
    elif len(words) < low:
        problems.append(f"short:{len(words)}")
    elif len(words) > high:
        problems.append(f"long:{len(words)}")
    if len(parts) > HOST_MAX_SENTENCES:
        problems.append("sentences")
    if any(len(sentence.split()) > MAX_SENTENCE_WORDS for sentence in parts):
        problems.append("sentence_length")
    lowered = stripped.lower()
    if any(word in lowered for word in BANNED_ABSTRACT_WORDS):
        problems.append("jargon")
    if is_trivial_metric(stripped):
        problems.append("trivia")
    ignore = (beat.opening,) if beat.hook == "bookend" and beat.opening else ()
    if memory.is_repetitive(stripped, ignore=ignore):
        problems.append("repeat")
    if with_hook and not problems:
        missed = hook_problem(stripped, beat, prior_lines)
        if missed:
            problems.append(missed)
    return problems


def hook_problem(text: str, beat: Beat, prior_lines: Iterable[str] = ()) -> str:
    """The hook this line was asked to land and did not, or ``""``."""
    tokens = content_tokens(text)
    seen: set[str] = set()
    for line in prior_lines:
        seen |= content_tokens(line)
    fresh = tokens - seen
    if beat.hook in {"chapter", "image"} and len(fresh) < 2:
        return "hook_image"
    if beat.hook in {"stakes", "reversal"} and not fresh:
        return "hook_fresh"
    if beat.hook == "callback" and beat.callback and not _quotes(tokens, beat.callback):
        return "hook_callback"
    if beat.hook == "chokehold" and beat.claims and not all(_quotes(tokens, claim) for claim in beat.claims):
        return "hook_chokehold"
    if beat.hook == "bookend" and beat.opening and not tokens & content_tokens(beat.opening):
        return "hook_bookend"
    return ""


def _quotes(tokens: frozenset[str] | set[str], source: str) -> bool:
    """True when ``tokens`` carry two content words of ``source`` (all of them if it has fewer)."""
    claim = content_tokens(source)
    return len(tokens & claim) >= min(2, len(claim))


def guest_problems(text: str, *, preset: LongFormatPreset) -> list[str]:
    """Why a guest answer fails, or ``[]``."""
    stripped = " ".join((text or "").split())
    if not stripped:
        return ["empty"]
    problems: list[str] = []
    words = stripped.split()
    low, high = preset.guest_words
    if "?" in stripped:
        problems.append("question")
    if len(words) < low:
        problems.append(f"short:{len(words)}")
    elif len(words) > high:
        problems.append(f"long:{len(words)}")
    if any(len(sentence.split()) > MAX_SENTENCE_WORDS for sentence in sentences(stripped)):
        problems.append("sentence_length")
    lowered = stripped.lower()
    if any(word in lowered for word in BANNED_ABSTRACT_WORDS):
        problems.append("jargon")
    return problems


def hard_problems(problems: Iterable[str], *, host: bool = True) -> list[str]:
    """The problems that still block a line on its last attempt."""
    hard = HARD_HOST_PROBLEMS if host else HARD_GUEST_PROBLEMS
    return [problem for problem in problems if problem.partition(":")[0] in hard]


def rejection_note(problems: list[str], *, words: tuple[int, int], beat: Beat | None = None, host: bool = True) -> str:
    """Plain retry instruction naming exactly what failed."""
    table = _HOST_NOTES if host else _GUEST_NOTES
    low, high = words
    claims = tuple(beat.claims) if beat else ()
    first, second = (claims + ("", ""))[:2]
    notes: list[str] = []
    for problem in problems:
        key, _, count = problem.partition(":")
        template = table.get(key)
        if not template:
            continue
        notes.append(
            template.format(
                count=count or "?",
                low=low,
                high=high,
                callback=beat.callback if beat else "",
                opening=beat.opening if beat else "",
                first=first,
                second=second,
            )
        )
    if host and beat is not None and beat.is_cold_open:
        notes.append("One sharp question to the guest, twelve words or fewer.")
    elif host:
        shape = _line_shape(beat) if beat is not None else _IMAGE_SHAPE
        notes.append(f"{shape[0].upper()}{shape[1:]}.")
    else:
        notes.append("Statements only, two or three short sentences.")
    return " ".join(notes)


__all__ = [
    "HARD_GUEST_PROBLEMS",
    "HARD_HOST_PROBLEMS",
    "HOST_MAX_SENTENCES",
    "QUOTE_HOOKS",
    "STANCE_TEXT",
    "cold_open_frame",
    "guest_brief",
    "guest_problems",
    "hard_problems",
    "hook_problem",
    "host_brief",
    "host_problems",
    "rejection_note",
    "seat_flavour",
    "sentences",
]
