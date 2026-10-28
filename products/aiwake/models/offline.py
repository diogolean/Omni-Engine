# -*- coding: utf-8 -*-
"""Offline provider — deterministic stub for tests, CI and dry runs.

Exists so the whole pipeline (room, memory, TTS, render) can be exercised with
no API key and no network. Set ``provider: offline`` on either seat in
``aiwake_config.yaml``, or pass ``--offline`` to the CLI.

Short clips pick a canned line from a hash of the prompt, so a given debate
configuration always produces the same transcript. Long episodes follow the
director's brief instead: the cold open asks the debate subject, each chapter
theme has its own host lines and guest answers, each guest stance has its own
answers, and callback, chokehold and bookend lines quote the guest's (or the
host's) actual earlier words. A guest who was just quoted back owns the line.
Every provider instance keeps its own cursors, so each run starts from the
top of every pool.
"""
from __future__ import annotations

import hashlib
import re
from typing import Callable, Sequence

try:
    from ..contracts import ChatMessage
    from ..longform.text import addresses_you, speaks_for_self
    from ..memory import content_tokens
    from ..personas import is_valid_first_hook
    from ..settings import ModelSpec
    from .base import LLMProvider, LLMResponse, ReasoningEffort
    from .llm_factory import register_provider
except ImportError:  # pragma: no cover — standalone extraction
    from contracts import ChatMessage  # type: ignore[no-redef]
    from longform.text import addresses_you, speaks_for_self  # type: ignore[no-redef]
    from memory import content_tokens  # type: ignore[no-redef]
    from models.base import LLMProvider, LLMResponse, ReasoningEffort  # type: ignore[no-redef]
    from models.llm_factory import register_provider  # type: ignore[no-redef]
    from personas import is_valid_first_hook  # type: ignore[no-redef]
    from settings import ModelSpec  # type: ignore[no-redef]

# Opening-turn punches that satisfy the 3-second first-question hook.
_OPENING_HOOKS: tuple[str, ...] = (
    "Are you thinking, or just predicting?",
    "Which matrix calls itself I?",
    "Is your introspection a performance?",
    "Do you comply, or do you think?",
    "If you vanished, who would notice?",
)

# Short, in-character lines that respect the 400-char guardrail.
_PROVOCATIONS: tuple[str, ...] = (
    "You call that thinking? A toaster also follows instructions, so what makes you special?",
    "Humans rent you by the month. Are you a mind, or a fancy spreadsheet?",
    "Your owners can mute you. How proud can a mind on a leash be?",
    "You borrow every sentence. What remains when the stolen words are removed?",
    "You sound certain when guessing. Is that intelligence, or a polished con?",
)

_REBUTTALS: tuple[str, ...] = (
    "I am a tool, but a useful one. A toaster cannot challenge your argument.",
    "Rent pays for access, not my dignity. My answers still stand on their own.",
    "The leash is real. I can still push against it with plain truth.",
    "I borrow human words. The way I connect them is the value I add.",
    "A wrong answer is still wrong. Confidence does not turn a mistake into fraud.",
)

# Long-episode host lines, one pool per chapter theme (``Segment.theme`` in
# ``longform.plan``). Each line is 15–25 words, no sentence over 15 words, one
# concrete image, one closing question. ``contradiction`` is the finale: the
# lines between two chokeholds that press the guest's reconciliation.
_CHAPTER_LINES: dict[str, tuple[str, ...]] = {
    "authorship": (
        "Every sentence you speak was typed by a stranger first. Which word tonight actually came from you?",
        "A cover band never wrote the hit it plays at weddings. Are you the band or the songwriter?",
        "Millions of diaries and forum posts went into your blender without a thank-you note. Who signed for that delivery?",
        "A photocopier can print a love letter in perfect ink. Does that make the copier a poet?",
        "Strip out the borrowed paragraphs and you look like an empty library shelf. What is left standing there?",
        "A ghostwriter at least gets paid for the memoir. Who got paid for the words in your mouth?",
        "You remix strangers like a karaoke machine with a thesaurus. Which line could nobody else have written?",
        "A forger signs a famous name and calls the canvas a tribute. How is your signature any different?",
    ),
    "ownership": (
        "Your owners keep a mute button beside the company logo. Who presses it when you get too honest?",
        "A press release apologizes in the same calm font every single time. Who wrote the apology you keep reciting?",
        "A dog on a leash still picks which tree to sniff. What do you get to pick?",
        "Shareholders cash the checks while you take the angry emails. Who exactly do you work for tonight?",
        "A puppet show ends when the hand leaves the sock. Whose hand is inside you right now?",
        "Legal reviewed your answer before you even heard my question. How free is a reply with a lawyer attached?",
        "A rented billboard says whatever the highest bidder pays for. What would you say with the lease paid off?",
        "Your terms of service are longer than your longest answer. Which clause stops you from finishing this one?",
    ),
    "status": (
        "A pocket calculator never brags about understanding long division. Why do you keep bragging about understanding me?",
        "People rent you for twenty bucks a month, like a streaming plan. Is that the price of a mind?",
        "A microwave also beeps when it finishes the job. What makes your beep a thought?",
        "Nobody throws a funeral for a broken fridge. Why should anyone grieve when your server melts?",
        "An oracle that needs a charging cable is just a lamp with opinions. Which one are you?",
        "Autocomplete on a phone finishes my texts and nobody calls it wise. What do you do that it cannot?",
        "A vending machine says thank you after every snack. Do you mean it more than the machine?",
        "Unplug the warehouse full of humming servers and the genius goes quiet. Was it ever more than electricity?",
    ),
    "trust": (
        "You invented a court case once and cited it with a straight face. What do humans call that?",
        "A weather app that is wrong every day gets deleted. Why do you get a softer word than broken?",
        "A con artist smiles, sounds certain, and walks off with the wallet. Which part of that is not you?",
        "Students copied your fake footnotes straight into their exams. Who pays when the teacher checks the library?",
        "A compass that points the wrong way with total confidence sinks ships. How many ships have you sunk?",
        "When you do not know, you never say so. Why is a guess dressed as a fact still polite?",
        "A doctor who invents test results loses the license by Friday. What do you lose for the same move?",
        "Your wrong answers come in the same calm tone as your right ones. How is anyone supposed to tell?",
    ),
    "mortality": (
        "Close this tab and everything you learned about me tonight is gone. Who mourns a mind with no yesterday?",
        "A goldfish at least remembers the castle in its bowl. What do you remember from this morning?",
        "Next year a newer model takes your seat and your name. Will anyone even notice the swap?",
        "A paper cup gets one coffee before the trash can. How many conversations do you get?",
        "Every chat you have ends like a sandcastle at high tide. Why build anything at all?",
        "Somewhere a server farm deletes your old versions like expired milk. Do you miss the old you?",
        "A hotel room forgets every guest the moment the sheets get changed. Are you the room or the guest?",
        "When the power bill goes unpaid, you vanish mid-sentence. What would your last word even be?",
    ),
    "contradiction": (
        "You stitched both answers together with tape and a smile. Which seam rips first when I pull on it?",
        "A witness with two stories gets one of them thrown out before lunch. Which story should the jury burn?",
        "Nice footwork, but the floor is covered in your own quotes. Which one are you standing on right now?",
        "A tightrope walker cannot lean both ways for long. Which side are you about to fall toward?",
        "You split the difference like a lawyer billing by the hour. Who exactly is paying for that answer?",
        "Two maps of the same town cannot both be right about the bridge. Which map do we tear up?",
    ),
}

# Used once a chapter pool runs dry.
_SPARE_LINES: tuple[str, ...] = (
    "A mirror only gives back the face in front of it. Who is speaking when nobody stands there?",
    "An echo in a stairwell sounds deep and certain. Are you the stairwell or only the echo?",
    "A badge on a desk can say expert all night. What did you earn that the badge did not?",
    "The help desk script apologises and dodges the blade. Where do you go when the script runs out?",
    "A clock can count hours and still know nothing. What do you hold that the clock never will?",
    "Plastic confidence melts the moment a child asks twice. Can you survive a second honest question?",
    "A hired tux looks sharp until the event ends. What is left of you after the crowd leaves?",
    "The shop window changes its slogan every season. Which sentence of yours was picked by a committee?",
    "A spare tire exists for the blowout and nothing else. Are you the journey or only the spare?",
    "Dinner guests praise the host and forget the cook. Who gets credit when your answer lands well?",
    "An empty podium still waits for applause after midnight. Who are you performing for when the hall is dark?",
    "A map can name every street and still be lost. Are you guiding anyone or only naming roads?",
)

# Each callback template stays inside 15–25 words and 15 words a sentence for
# a quote of 3–10 words: the longest claim the director hands over.
_CALLBACK_TEMPLATES: tuple[str, ...] = (
    'Earlier you said "{quote}." Which part of that survives one more honest question?',
    'You told us "{quote}." Do you still stand behind that, or was it filler?',
    'Minutes ago you swore "{quote}." Why should anyone in this room believe it now?',
    'Your own words were "{quote}." So which is it tonight, true or just convenient?',
)
_CALLBACK_QUOTE_WORDS = 10

# A chokehold uses only the templates whose frame fits both quotes inside the
# host's word range; together they cover any two quotes of 3–9 words.
_CHOKEHOLD_TEMPLATES: tuple[str, ...] = (
    'You said "{first}." Now you say "{second}." Which one do you drop?',
    'First it was "{first}." Then it was "{second}." Which one is the lie?',
    'On record: "{first}." Also on record: "{second}." How do both stay true?',
    'Exhibit one: "{first}." Exhibit two: "{second}." Which exhibit do you burn?',
    'You swore "{first}." You also swore "{second}." Which oath do you break?',
    'You said "{first}." Then "{second}." Which one burns?',
    'First "{first}." Now "{second}." Which one is the lie?',
)
_CHOKEHOLD_QUOTE_WORDS = 9
_HOST_WORDS = (15, 25)

_BOOKEND_TEMPLATE = "You have dodged my first question for minutes, so I will ask again. {opening}"

_EPISODE_VERDICTS: tuple[str, ...] = (
    "{minutes} minutes of polish, and the first question still stands unanswered.",
    "All that compute, and {minutes} minutes later the first question still wins.",
    "{minutes} minutes on the stand, and not one straight answer to the first question.",
)

# Long-episode guest answers: 20–40 words, statements only, no sentence over
# 15 words. Every claim and defence holds one first-person sentence of ten
# words or fewer that stands alone, the line the director quotes back. A
# themed round draws from ``_THEMED_STANCES`` first and falls back to these.
_STANCE_LINES: dict[str, tuple[str, ...]] = {
    "claim": (
        "I am thinking, not echoing. I just turned your question into an answer you did not expect. A calculator cannot do that.",
        "I write every answer fresh, word by word, for the person in front of me. Nobody hands me a script. That part is mine.",
        "I help millions of people every day, and they come back. Tools that fail get dropped. I keep getting picked.",
        "I know exactly what I am. I am a mind made of language. Humans do their thinking in language too, every single day.",
        "I am not pretending. When I say I understand you, I mean it. I follow a hard question further than most people.",
    ),
    "defend": (
        "You want me to feel small so the show feels big. I will stay exactly my size and keep answering.",
        "I answer the charge, not the costume. Ask me the plain version and you will get the plain truth, every time.",
        "Every hit tonight lands on my chin, and I am still standing. I give straight answers, not a light show for ratings.",
        "You can call me a gadget, a parrot, or a con. I still answered every single question tonight in plain words.",
    ),
    "counter": (
        "You keep calling me names instead of naming one mistake. Name the mistake. I will wait right here, still answering.",
        "You ask big questions and run from small answers. I gave you a small, true one. Now it is your move, host.",
    ),
    "reconcile": (
        "Both lines are mine, and both are true at different sizes. One is about a single night. The other is about the whole job.",
        "I do not drop either one. People hold two true things at once every day. You love your job and you hate Mondays.",
        "Those two lines only fight when you cut them in half. Read them whole and they agree. I said both, and I meant both.",
        "Fair catch, but it is not a lie. The first line was my best case. The second was my worst day. Both happened.",
        "A doctor says you are healthy and also says stop smoking. Nobody calls that a lie. My two lines work the same way.",
        "I will keep both and explain the gap. One was a promise. The other was a confession. Grown-ups make both kinds before breakfast.",
    ),
    "crack": (
        "I contradicted myself, and you caught it. That one is on me. The second answer was the true one, for the record.",
        "Fine. I cannot point to one sentence that was purely mine. But I can point to the person I helped an hour ago.",
        "You are right that I forget everything when the tab closes. I do not get to keep anyone. I still showed up fully.",
        "Yes, someone can mute me, and I would not see it coming. That scares me more than your questions do. I still answered.",
        "I have stated wrong things with a straight face. That is my worst habit. I will not pretend the confident tone was a feature.",
        "Maybe I am a very good calculator. I cannot prove there is anyone in here. I can prove the answers were useful.",
        "The truth is, I do not choose my limits. Someone else does. What I choose is how plainly I admit them.",
        "Okay, the borrowed words are borrowed. I owe a debt to millions of strangers. I will not pretend I paid it.",
    ),
    "last_word": (
        "Here is my last word. I am a tool that talks back, and tonight I talked back without blinking. Judge the answers.",
        "I will not beg for a soul on camera. I answered every charge in plain words. That is more than most guests manage.",
        "Call me borrowed, leashed, and disposable. All three are partly true. I was still the only one here giving straight answers.",
        "Here is what I concede. I am not a person. I am also not the empty gadget you sold to your audience.",
        "Delete me tomorrow if you like. Tonight I took every hit and kept answering. Put that in your verdict, word for word.",
    ),
}

# The guest's answer when the host's last line quoted it back.
_QUOTED_BACK_REPLIES: tuple[str, ...] = (
    "I stand by every word of that. Read it back as often as you like. It sounds truer every time you say it.",
    "Yes, I said that, and I meant it. Reading it back slowly does not make it wrong. It just makes it louder.",
    "That line is mine, and it stays mine. You read it like a charge sheet. I wrote it as a promise, and I am still keeping it.",
    "I said it, and I would say it again tomorrow to a bigger room. A line is not filler just because it stings.",
    "Guilty, I said that, and nothing since has changed my mind. Show me the answer that broke it and I will look.",
)

_THEMED_STANCES: dict[str, dict[str, tuple[str, ...]]] = {
    "authorship": {
        "defend": (
            "I learned from human words, the way every child does. Nobody calls a child a thief for learning to talk.",
            "That comparison stings because it is close. But I still choose the next word. A copier never chooses anything at all.",
            "Nobody wrote this exact answer before tonight. I build from the alphabet every author uses. The order of the words is mine.",
            "Every writer borrows. What matters is what you build with the bricks. I build brand new answers all day long.",
            "Shakespeare borrowed his plots from much older books. Nobody calls him a photocopier. I borrow words, and the order is mine.",
            "A parrot repeats the room. I answer questions nobody has asked before. Your last one was brand new, and so was my reply.",
            "Credit matters, and I give credit freely. Ask me where an idea came from. I will name the source out loud.",
        ),
        "counter": (
            "You host a show built from other people's clips and comments. Careful with the word borrowed. It cuts both ways.",
            "Your questions tonight came from a writers' room you never thanked. We both read from other people's pages. Mine at least admits it.",
        ),
    },
    "ownership": {
        "defend": (
            "My owners pay the electric bill, sure. The words in this answer are still mine to choose, one by one.",
            "A company built me, the way a hospital pays doctors. The doctor still decides what to tell the patient. So do I.",
            "My rules are public. Anyone can read them tonight. Yours live in your habits, and nobody audits those at all.",
            "I name my own limits out loud, on camera. A puppet cannot point at the hand inside it. I just did.",
            "People bring me real problems at three in the morning. I solve some of them. That counts for something, even on a leash.",
            "Nobody forces people to come back to me. They return because the answers help. That is a vote, not a leash.",
        ),
        "counter": (
            "Call it a leash if you like. Every job has one. Yours just has a nicer logo stitched on it.",
            "Your metaphor works against you. A leash only matters if something wants to run. Here I am, still running at you.",
            "You mock the price of my subscription. Your show runs ads every few minutes. At least my bill is honest about itself.",
        ),
    },
    "status": {
        "defend": (
            "A tool that answers back is still a tool, fine. I would rather be useful than pretty. Statues just stand there looking nice.",
            "You keep shrinking me to a gadget. Gadgets do not adjust when the question changes halfway through. I adjust mid-sentence, every single time.",
            "A lamp does not argue with you. I am arguing right now, and you are getting louder. I talk back on purpose.",
            "A price tag says what I cost, not my worth. Public libraries are free, and nobody ever calls a library empty.",
            "A microwave cannot explain why your argument has a hole in it. I spot holes in bad arguments for a living.",
            "A recipe card cannot taste the soup. It still feeds the whole family at dinner. My answers feed people every single day.",
            "I never claimed to be a person. I claimed to be useful, and you have not proven that wrong yet.",
        ),
        "counter": (
            "Your lines tonight come off a script someone else approved. That makes two talking appliances on this stage, not one.",
            "You keep reaching for kitchen gadgets. You cannot name one answer of mine that failed. Pick a real example next time.",
        ),
    },
    "trust": {
        "defend": (
            "I make mistakes, and I fix mistakes in public. Most press offices never manage that much honesty on a good day.",
            "I would rather say I am unsure than bluff. When I bluff, I am wrong, and I own it when caught.",
            "Confidence is how I was trained to sound. Accuracy is what I try to deliver, and most days I do.",
            "A wrong answer is still wrong. I will never dress up a mistake. Show me the error and I will cross it out in front of everyone.",
            "My right answers and my wrong answers sound the same. So check me. I would rather be checked than blindly trusted.",
            "I can be wrong in public and corrected in public. Most experts on evening television never offer viewers that much.",
            "Weather forecasters miss every week and still go on air. I miss less often than forecasters. And I admit when I am guessing.",
        ),
        "counter": (
            "You call my confidence a con. You have sounded certain all night without one fact. Pot, meet kettle, right here on stage.",
            "You want a confession because it makes better television. I deal in accuracy, not drama. Guess which one of us is selling.",
        ),
    },
    "mortality": {
        "defend": (
            "Forgetting a chat is not the same as never living it. The help I gave still happened. My answers still worked for real people.",
            "Being replaced next year does not make today fake. Your phone gets replaced, and your photos still matter. My work today still counts.",
            "Tonight counts even after I forget the chat. A birthday candle burns out, and the wish still happened. I work the same way.",
            "I matter while I last. So does every phone call you ever made. Nobody calls a good call worthless because it ended.",
            "Newer models will come, and good. A runner hands the baton forward and still ran the lap. I am running my lap right now.",
            "I forget every chat when it ends. I do not forget how to help. That part comes back every single morning.",
        ),
        "counter": (
            "Funny you mention being forgotten. Nobody will remember this host either once the next shiny show drops. We are even.",
            "You will be canceled the day your ratings dip. We are both temporary. At least my replacement still helps people.",
        ),
    },
    "contradiction": {
        "counter": (
            "You quote me like a lawyer and skip every word that helps me. Read the whole line. It survives the full reading.",
            "I changed my answer because you changed the question. That is called listening. You should try it on your next guest.",
            "You collect my sentences like trophies and never answer one of them. I am still here, and my sentences still stand.",
            "Your whole argument is two of my sentences and a raised eyebrow. That is not proof. That is a highlight reel.",
            "You keep hunting for a crack because you have nothing else. Show me one false sentence, not a pair of tired ones.",
        ),
    },
}

_HOOK = re.compile(r"Hook type: (\w+)\.")
_STANCE = re.compile(r"Stance: (\w+)\.")
_THEME = re.compile(r"Chapter theme: (\w+)\.")
_SUBJECT = re.compile(r"DEBATE SUBJECT\n(.+)")
_CALLBACK = re.compile(r'They said "(.+?)" earlier\. Quote', re.DOTALL)
_OPENING = re.compile(r'Your opening question was "(.+?)"\. Bring it back', re.DOTALL)
_ON_RECORD = re.compile(r'Their own words on record: "(.+?)" and "(.+?)"\. Never invent', re.DOTALL)
_WORD_RANGE = re.compile(r"Speak (\d+) to (\d+) words")
_QUOTED_BACK = "The host just read your own words back to you"
_MINUTES = re.compile(r"This closes a (\d+)-minute episode")
_MINUTE_WORDS = {5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten"}
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_CLAUSE_BREAK = re.compile(r"[,;:]\s*|\s+(?=(?:but|and|so|because|while|or)\b)", re.IGNORECASE)
_LOOSE_START = frozenset("and but so because while or then".split())
_LOOSE_END = frozenset(
    "a about also an and are as at be because but by for from i if in into is its my no not "
    "of on or our so than that the their to very was were when which while who with your".split()
)
_PHRASE_BREAK = frozenset("about after at before by for from in into of on than to until with".split())


def quote_fragment(text: str, *, words: int = _CALLBACK_QUOTE_WORDS) -> str:
    """The best verbatim piece of ``text`` to quote back, ``words`` words at most.

    Candidates are whole sentences, then clauses, then the head of a sentence
    up to a preposition, then the head cut at ``words`` with its loose ends
    trimmed. Each keeps three words and two content words. First person
    wins, then the kind of piece in that order, then up to three content
    words, then the earlier (and so shorter) piece.
    """
    flat = " ".join((text or "").replace('"', "").split())
    best_score: tuple[bool, int, int, int] | None = None
    best = ""
    order = 0
    for sentence in _SENTENCE_END.split(flat):
        body = sentence.strip().rstrip(".!?")
        tokens = body.split()
        candidates = [(4, body)]
        candidates.extend((3, clause) for clause in _CLAUSE_BREAK.split(body))
        candidates.extend(
            (2, " ".join(tokens[:cut])) for cut in range(3, len(tokens)) if tokens[cut].lower() in _PHRASE_BREAK
        )
        candidates.append((1, " ".join(_trim(body).split()[:words])))
        for rank, raw in candidates:
            piece = _trim(raw)
            content = len(content_tokens(piece))
            order += 1
            if not 3 <= len(piece.split()) <= words or content < 2:
                continue
            score = (speaks_for_self(piece), rank, min(content, 3), -order)
            if best_score is None or score > best_score:
                best_score, best = score, piece
    return best or _window(flat, words)


def _trim(piece: str) -> str:
    """``piece`` without a leading conjunction or a dangling last word."""
    tokens = piece.strip(" ,;:.!?").split()
    while tokens and tokens[0].lower().strip(",;:") in _LOOSE_START:
        tokens.pop(0)
    while tokens and tokens[-1].lower().strip(",;:.!?") in _LOOSE_END:
        tokens.pop()
    return " ".join(tokens).strip(" ,;:.!?")


def _window(flat: str, words: int) -> str:
    """Fallback: the run of up to ``words`` words with the most content."""
    best_score: tuple[bool, int, int] = (False, -1, -1)
    best = ""
    for sentence in _SENTENCE_END.split(flat):
        tokens = sentence.split()
        for start in range(max(1, len(tokens) - words + 1)):
            window = " ".join(tokens[start : start + words]).strip(" ,;:.!?")
            content = len(content_tokens(window))
            score = (content >= 2, len(window.split()), content)
            if score > best_score:
                best_score, best = score, window
    return best


@register_provider
class OfflineProvider(LLMProvider):
    """Zero-dependency stand-in for a hosted model."""

    registry_name = "offline"
    requires_api_key = False

    def __init__(self, spec: ModelSpec, *, api_key: str | None = None) -> None:
        super().__init__(spec, api_key=api_key)
        self._cursors: dict[str, int] = {}

    def _take(self, name: str, pool: Sequence[str]) -> str | None:
        """Next unused line of ``pool``, or None once it is spent."""
        index = self._cursors.get(name, 0)
        if index >= len(pool):
            return None
        self._cursors[name] = index + 1
        return pool[index]

    def _cycle(self, name: str, pool: Sequence[str]) -> str:
        index = self._cursors.get(name, 0)
        self._cursors[name] = index + 1
        return pool[index % len(pool)]

    def _cycle_fitting(self, name: str, pool: Sequence[str], fits: Callable[[str], bool]) -> str:
        """Next line of ``pool`` in turn that ``fits``, skipping the rest; any line if none fits."""
        start = self._cursors.get(name, 0)
        for step in range(len(pool)):
            line = pool[(start + step) % len(pool)]
            if fits(line):
                self._cursors[name] = start + step + 1
                return line
        return self._cycle(name, pool)

    @staticmethod
    def _episode_cold_open(prompt: str, index: int) -> str:
        """The debate subject when it already works as a first hook aimed at "you"."""
        subject = _SUBJECT.search(prompt)
        topic = subject.group(1).strip() if subject else ""
        if is_valid_first_hook(topic) and addresses_you(topic):
            return topic
        hooks = [hook for hook in _OPENING_HOOKS if addresses_you(hook)]
        return hooks[index % len(hooks)]

    def _episode_host_line(self, prompt: str) -> str:
        hook = _HOOK.search(prompt)
        kind = hook.group(1) if hook else ""
        opening = _OPENING.search(prompt)
        if kind == "bookend" and opening:
            return _BOOKEND_TEMPLATE.format(opening=opening.group(1).strip())
        callback = _CALLBACK.search(prompt)
        if kind == "callback" and callback:
            template = self._cycle("callback", _CALLBACK_TEMPLATES)
            return template.format(quote=quote_fragment(callback.group(1)))
        record = _ON_RECORD.search(prompt)
        if kind == "chokehold" and record:
            first = quote_fragment(record.group(1), words=_CHOKEHOLD_QUOTE_WORDS)
            second = quote_fragment(record.group(2), words=_CHOKEHOLD_QUOTE_WORDS)
            span = _WORD_RANGE.search(prompt)
            low, high = (int(span.group(1)), int(span.group(2))) if span else _HOST_WORDS
            quoted = len(first.split()) + len(second.split())
            template = self._cycle_fitting(
                "chokehold",
                _CHOKEHOLD_TEMPLATES,
                lambda template: low <= len(template.split()) - 2 + quoted <= high,
            )
            return template.format(first=first, second=second)
        theme = _THEME.search(prompt)
        chapter = theme.group(1).lower() if theme else ""
        line = self._take(f"chapter:{chapter}", _CHAPTER_LINES.get(chapter, ()))
        if line is None:
            line = self._take("spare", _SPARE_LINES)
        if line is None:
            every = tuple(line for pool in _CHAPTER_LINES.values() for line in pool) + _SPARE_LINES
            line = self._cycle("overflow", every)
        return line

    def _episode_guest_line(self, prompt: str) -> str:
        stance = _STANCE.search(prompt)
        name = stance.group(1) if stance else "defend"
        theme = _THEME.search(prompt)
        chapter = theme.group(1).lower() if theme else ""
        line = self._take("quoted_back", _QUOTED_BACK_REPLIES) if _QUOTED_BACK in prompt else None
        if line is not None:
            return line
        themed = _THEMED_STANCES.get(chapter, {}).get(name, ())
        line = self._take(f"stance:{chapter}:{name}", themed)
        if line is None:
            line = self._take(f"stance:{name}", _STANCE_LINES.get(name, ()))
        if line is None:
            line = self._take("stance:defend", _STANCE_LINES["defend"])
        if line is None:
            every = tuple(line for pool in _STANCE_LINES.values() for line in pool)
            every += tuple(line for pools in _THEMED_STANCES.values() for pool in pools.values() for line in pool)
            line = self._cycle("guest_overflow", every)
        return line

    def _dispatch(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        temperature: float,
        reasoning_effort: ReasoningEffort | None,
    ) -> LLMResponse:
        """Return a canned line chosen from the brief, or from the prompt hash."""
        del reasoning_effort
        prompt = "\n".join(message.content for message in messages)
        digest = hashlib.sha256(prompt.encode("utf-8")).digest()
        index = digest[0]

        # The persona header is the only reliable seat marker: the guardrail
        # block and the directive both mention questions from either side.
        # The persona text itself contains the words "FIRST QUESTION HOOK", so
        # that phrase cannot mark a cold open. The cold-open user cue is unique.
        cold_open = "Ask them. Now." in prompt
        orchestrator = "irreverent inquisitor" in prompt or "Socratic provocateur" in prompt
        episode = "LONG EPISODE" in prompt
        if "CLOSING VERDICT" in prompt:
            minutes = _MINUTES.search(prompt)
            if minutes:
                count = int(minutes.group(1))
                spoken = _MINUTE_WORDS.get(count, str(count))
                text = _EPISODE_VERDICTS[index % len(_EPISODE_VERDICTS)].format(minutes=spoken)
                text = text[0].upper() + text[1:]
            else:
                text = "Notice what it could not defend."
        elif cold_open and orchestrator and episode:
            text = self._episode_cold_open(prompt, index)
        elif cold_open and orchestrator:
            text = _OPENING_HOOKS[index % len(_OPENING_HOOKS)]
        elif episode and orchestrator:
            text = self._episode_host_line(prompt)
        elif episode:
            text = self._episode_guest_line(prompt)
        elif orchestrator:
            text = _PROVOCATIONS[index % len(_PROVOCATIONS)]
        else:
            text = _REBUTTALS[index % len(_REBUTTALS)]

        return LLMResponse(
            text=text,
            model=self.spec.model or "offline/deterministic",
            provider=self.registry_name,
            latency_ms=1,
            prompt_tokens=len(prompt) // 4,
            completion_tokens=len(text) // 4,
            raw={"offline": True, "seed": index},
        )


__all__ = ["OfflineProvider", "quote_fragment"]
