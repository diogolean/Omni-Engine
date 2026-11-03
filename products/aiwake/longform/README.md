# Aiwake long-form

Horizontal YouTube episodes of 5 to 10 minutes, plus two or three vertical Shorts cut from each one.

The debate room, the judge, the voices, and the avatar renderer are the existing Aiwake pipeline. This package gives a long episode its shape and its pacing: it plans the chapters, directs every exchange on the episode clock, writes the briefs and checks each line, keeps the seats on the cheap models, writes the YouTube copy, makes the Shorts, prices the run, and blocks a file that is not ready to publish. The ffmpeg avatar engine (`animation.parametric`) renders the picture, not MoviePy.

## Render one 6-minute test episode and its Shorts

On Diogo's PC, from the repo root, with the requirements installed, ffmpeg on `PATH`, and the puppet assets in place:

```bash
python tools/channel_cli.py --channel aiwake longform --offline --target-duration 360
```

`--offline` swaps both seats for the deterministic stub, so the language-model bill is **$0**. Edge TTS speaks the lines (free, needs a network connection) and ffmpeg writes this folder:

```text
{OUTPUT_PATH}/aiwake/animation_clips/long_format/<session>/
  episode_<session>.mp4     1920x1080, about 6 minutes
  thumbnail_<session>.png
  metadata.json             title, description, chapters, tags, plan, director beats, cost, QA report
  shorts/short_01.mp4       1080x1920, 15-59 s
  shorts/short_01.json      the Short's own title, description, and CTA
  shorts/short_02.mp4 ...
  shorts/manifest.json
```

The live run is the same command without `--offline`. It seats **DeepSeek Chat** as the host against **Llama 3.3 70B** as the guest, the cheap pair in `aiwake_config.yaml`, and needs `OPENROUTER_API_KEY` in `.env`.

When the run ends, the CLI prints the video, metadata, and Shorts paths, the `cost estimate` and `cost actual` lines, and a `qa` line that reads `pass` or `FAIL`, followed by one line per failure and per warning. A failed gate exits with code 1.

Useful flags: `--target-duration 300..600`, `--shorts 2|3`, `--topic "..."`, `--orchestrator` / `--target` (aliases), `--random-matchups` (draws from `cheap_roster` only), `--quantity N`, `--no-audio` (a voiceless preview, which the gate always fails), `--no-video` (transcript, plan, and metadata only; skips the media checks), `--preview`. `python tools/channel_cli.py --channel aiwake longform --help` lists them all.

## Cost per episode

Language-model tokens only. Edge TTS and the local ffmpeg render cost nothing. These are planning estimates from `cost.py`: the planned exchanges times the measured prompt and reply sizes, with 50% headroom for retried lines.

| Target | DeepSeek Chat + Llama 70B (default) | Gemini Flash + Llama 70B | Claude Sonnet + Llama 70B (opt-in) |
|---|---|---|---|
| 5 min (300 s) | $0.019 | | $0.17 |
| 6 min (360 s) | **$0.026** | $0.034 | $0.22 |
| 8 min (480 s) | $0.033 | | $0.28 |
| 10 min (600 s) | $0.045 | | $0.38 |

`cost actual` prices the token counts each provider returned for this run, at the same configured rates, and `metadata.json` breaks it down by seat. An offline 6-minute ledger priced at the default pair's rates comes to about $0.017, under the estimate because the stub never needs a retry.

Claude, GPT-4o, Gemini Pro, and DeepSeek R1 stay off until `long_format.allow_expensive` is `true`. Seating one of them without that flag exits with an error before any call is made. A model slug with no configured rate is priced like Claude, so an unknown seat never looks cheap.

## The shape of an episode

| Segment | What happens |
|---|---|
| Cold open | One exchange. The host puts the subject to the guest as one question of twelve words or fewer, with "you" in it. That line is also the title candidate. The guest plants a claim about itself. |
| Rounds | One per `round_target_s` (95 s) of debate time, two at least, at most `chapters` (5). Each round owns one attack angle from the provocation bank: authorship, ownership, status, trust, or mortality. Each round takes at least two exchanges and none is skipped. |
| Chokehold | Always the last round. The host sets two of the guest's own earlier lines side by side until one has to go. No new topic. |
| Verdict | One exchange. The host brings back the cold-open question in new words and demands the answer. The guest gets the last word, then the closing verdict line. |

| Target | Rounds, including the chokehold | Exchanges planned |
|---|---|---|
| 5 min | 3 | 16 |
| 6 min | 3 | 20 |
| 7 min | 4 | 21 |
| 8 min | 5 | 24 |
| 10 min | 5 | 30 |

The planned exchange count drives the cost estimate. A timed episode does not stop on it: `director.py` keeps an episode clock from the measured voice length of every line (a word-rate estimate when audio is off) and moves to the next chapter by time, so the verdict lands near the target length whether the models talk short or long. `max_turns` (48) is the hard cap either way, and the chokehold is always reached before the verdict.

## How the dialogue is directed

Before every exchange the director decides the chapter, the intensity, whether this line must land a retention hook, and the guest's stance.

**Hooks.** A hook lands about every `hook_interval_s` (30 s) and never more than `hook_max_gap_s` (45 s) after the last one. Before each exchange starts, the director assumes it may run 30% longer than the exchanges so far, and for the first few exchanges no shorter than planned, so one long answer cannot push the gap past 45 s. The hook types:

| Hook | When | What the host must do |
|---|---|---|
| `open` | Cold open | One sharp question, twelve words or fewer. |
| `chapter` | First line of each round | An image nobody has used yet, aimed at the round's goal. |
| `callback` | Rotation | Quote one of the guest's earlier claims word for word and turn it against them. |
| `stakes` | Rotation | Say what the guest loses right now if its last answer is wrong. |
| `image` | Rotation | A concrete object nobody has mentioned yet. |
| `reversal` | Rotation | Grant the last point in four words or fewer, then show why it hurts. |
| `chokehold` | Every other finale exchange | Quote a few exact words of two earlier lines and demand the guest drop one. |
| `bookend` | Verdict exchange | Bring back the cold-open question and demand the answer. |

Each hook is checked on the line itself. A chapter or image hook needs two content words no earlier host line used, a stakes or reversal hook needs one, a quote hook needs two content words of every line it quotes, and the bookend needs a word of the cold open. A miss gets a retry note that names exactly what to fix.

**Quotes that stand alone.** The guest brief asks for one sentence about itself, starting with "I" or "My", ten words or fewer. The director pulls that sentence out of every answer (`self_claim`): first person, four to ten words, not aimed at "you", and with no word like "it", "that", or "they" that leans on an earlier line. Callbacks and chokeholds quote that sentence, so a viewer who missed the minute it came from still understands it. When an answer has no such sentence, the host quotes at least three of its words exactly. The same claim is never called back twice or pitted twice.

**Escalation.** Intensity climbs with the share of the clock already spent, from 1 to 4, so minute five is meaner than minute one. The verdict exchange is always 4.

**Guest stances.** The guest plants a claim in the cold open and defends it with concrete examples. It counter-punches every third answer of a round (the first round defends twice before it hits back). In the chokehold it alternates between reconciling the two quoted lines and hitting back, holds out, and cracks on the last finale exchange: one real limit admitted, one thing kept. The director decides before that exchange starts that it is the last one, so the crack always lands before the verdict.

**Character voices.** Each brief tells the model who it is on screen, from `character_voices`: DeepSeek is a terse engineer proud of doing more for less, Llama a plainspoken open-weights underdog, Gemini a polished corporate spokesmodel, and so on. The host needles what the guest is.

## Line contracts and retries

| | Host | Guest |
|---|---|---|
| Words | 15-25 (cold open: 12 or fewer, 80 characters at most) | 20-40 in two or three sentences |
| Sentences | 3 at most, each under 16 words | each under 16 words |
| Questions | ends on exactly one `?` | none |
| Also | no repeat of an earlier question, no AI jargon, no trivia about voices, copyright, or parameter counts | no AI jargon |

Each brief says that its word range overrides the 25-word short-form cap in the persona, so a cheap model never sees two limits that contradict each other.

Every line gets `generation_attempts` (3) tries. The last try only has to meet the hard contract: for the host, a complete line with one question, no word indexing, and no trivia; for the guest, a complete answer. A line that still misses its hook or its word range ships, and the miss is recorded in `metadata.json` and shown as a QA warning. A ten-minute episode is never thrown away at minute seven over one soft miss.

## YouTube copy

- **Title:** `"<verbatim line>" | DeepSeek vs Llama (Full Debate)`, 70 characters or fewer. It is the cold-open question when that fits, otherwise the best round question or guest comeback ranked by how well it stands alone. A quote is a whole sentence someone said. It is never cut to fit.
- **Description:** the cold-open quote, one line on the fight ("DeepSeek puts Llama on the stand for 6 minutes: three rounds, then a verdict."), the guest's own words from the final round, the question, the chapter list, the pick-a-side line, and exactly three hashtags.
- **Chapters:** each starts where that chapter's first question starts on the rendered timeline. They are merged until YouTube will keep them: three or more, the first at `0:00`, each at least ten seconds long.
- **Tags:** within YouTube's 500-character budget. The reserved words (money, profit, business, conspiracy, consciousness, illusion) appear only when the episode actually said them.

## Shorts

`choose_highlights` scores every run of whole exchanges that opens on a host line and closes on a guest answer. The score rewards the hook that opens the run, the strongest guest stance inside it (a crack, then a counter-punch), and the chokehold round. It keeps 2 or 3 windows that do not overlap, from different chapters first. The verdict exchange stays out of every Short, so the long video keeps its ending.

`shorts_mode: regenerate` (the default) renders each window again at 1080x1920, with the same puppets, arena, skin, and voice files as the episode. When a re-render fails or plays a line as silence, or with `shorts_mode: cut`, the Short is a bounded slice of the master laid over a blurred copy of itself, so both fighters and the subtitles stay in frame.

Each Short gets its own quote title (never the same quote as the long title), a description, three hashtags, and this CTA line:

```text
Full debate on the channel: "<quote>" | DeepSeek vs Llama (Full Debate)
```

## Pre-publish gate

`qa.py` blocks publishing when:

- the long file is missing, is not 1920x1080, runs outside `[max(300, target - 120), min(600, target + 120)]` seconds (about 300-480 s for a 6-minute target), or has no audio stream or a silent one (its loudest sample at or under -60 dBFS);
- the debate stopped early or never finished the verdict exchange;
- a spoken line has no voice track (TTS gave up on it, or the run used `--no-audio`), or the renderer could not decode its voice file and played silence under the subtitle;
- YouTube would drop the chapters;
- there are not 2 or 3 Shorts, or a Short is under 15 s, over 59 s, without sound, or not vertical.

Warnings do not block publishing: the longest gap between hooks went over 45 s on the rendered timeline, a hook did not land, a line shipped on its last try with a soft miss, the episode reached the verdict without the chokehold, or a Short was cut from the master because its re-render failed.

## ffmpeg stays bounded

Every ffmpeg and ffprobe call in this package goes through `ffmpeg_guard.run_bounded`, which refuses a command that could run forever before it starts:

- `amix` must set `duration=first` or `duration=shortest`;
- `apad` must set `whole_dur`, `whole_len`, `pad_dur`, or `pad_len`, unless the output is cut with `-t`, `-to`, or `-frames`;
- a `lavfi` generator, or an input looped with `-stream_loop -1` or `-loop 1`, needs a duration or a `-t`/`-frames` limit.

Each call then runs with a timeout that kills the whole process group (`shared.ffmpeg.run_ffmpeg`). The frame pipe in `animation/parametric/renderer.py` kills ffmpeg when frame generation raises, and the CLI stops any ffmpeg it started that is still alive when it exits. Teaser cuts put `-ss` and `-t` before the input, so ffmpeg never decodes the whole episode to cut a Short.

## Config

Everything lives in `products/aiwake/aiwake_config.yaml` under `long_format`, loaded by `products.aiwake.settings.load_settings`:

| Key | Default | Meaning |
|---|---|---|
| `target_duration_s` | 360 | Default length. `--target-duration` overrides it (300-600). |
| `chapters`, `round_target_s` | 5, 95 | Most rounds, and seconds of debate per round. |
| `host_words`, `guest_words` | [15, 25], [20, 40] | Words per line. |
| `hook_interval_s`, `hook_max_gap_s` | 30, 45 | Hook pacing. |
| `generation_attempts` | 3 | Tries per line. |
| `default_orchestrator`, `default_target` | deepseek-chat, llama-70b | The cheap pair. |
| `cheap_roster` | deepseek-chat, llama-70b, gemini-flash | Seats `--random-matchups` may draw. |
| `allow_expensive` | false | Gate for Claude, GPT-4o, Gemini Pro, and DeepSeek R1. |
| `shorts_count`, `shorts_mode` | 3, regenerate | Teasers per episode and how they are made. |
| `shorts_min_s`, `shorts_target_s`, `shorts_max_s` | 15, 36, 59 | Teaser length. |
| `character_voices` | per model family | The on-screen character note in each brief. |
| `model_rates` | per alias | USD per million tokens for the estimate and the measured bill. |

`python -m products.aiwake --mode longform --resolution 1920x1080 --target-duration 360` runs the same pipeline. Without the long-form flags, the vertical 9:16 flow is unchanged.

## Tests

```bash
python -m pytest products/aiwake/tests/test_longform_episode.py products/aiwake/tests/test_longform_youtube.py -q
python -m pytest products/aiwake -q
python -m pytest -q
```

`test_longform_episode.py` covers the plan, the director's clock, hooks, stances, quotes, and crack, the briefs and line checks, the offline stub, and whole offline episodes from 5 to 10 minutes. `test_longform_youtube.py` covers the YouTube copy and chapters, the cost, the QA gate, the Shorts, the ffmpeg guard, the CLI, and the publish step. Neither file needs a network connection or an API key.
