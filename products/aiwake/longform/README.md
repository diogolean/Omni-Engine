# Aiwake long-form

Horizontal YouTube episodes, 5 to 10 minutes, plus two or three vertical Shorts cut from each one.

The debate, the judge, the voices, and the avatar renderer are the existing Aiwake pipeline. This package plans the chapters, keeps the seats on the cheap models, writes the YouTube metadata, cuts the teasers, and blocks a file that is not ready to publish. The picture is rendered by the ffmpeg avatar engine (`animation.parametric`), not by MoviePy.

## Render one 6-minute test episode and its Shorts

On Diogo's PC, from the repo root, with ffmpeg on `PATH` and the puppet assets in place:

```bash
python tools/channel_cli.py --channel aiwake longform --offline --target-duration 360
```

`--offline` uses the deterministic stub, so the language-model bill is **$0**. Edge TTS speaks the lines (free, needs a network) and ffmpeg writes:

- `outputs/aiwake/animation_clips/long_format/<session>/episode_<session>.mp4` at 1920x1080
- `metadata.json` with the title, the description, the chapter lines, and the tags
- `shorts/short_01.mp4`, `short_02.mp4`, `short_03.mp4` at 1080x1920

Each teaser description contains:

```text
Full debate on the channel: "<episode title>".
```

The same command without `--offline` seats **DeepSeek Chat** against **Llama 70B** (the cheap pair in `aiwake_config.yaml`). Planning estimate for that 6-minute episode: **$0.027** in language-model tokens. A 10-minute target (`--target-duration 600`) is about **$0.05**. Edge TTS and the local render are not in that figure.

Claude, GPT-4o, Gemini Pro, and DeepSeek R1 stay off until `long_format.allow_expensive` is `true` in `products/aiwake/aiwake_config.yaml`. Seating one of them without that flag exits with an error. Swapping the orchestrator to Claude on the same 6-minute plan is about **$0.22**.

`--no-video` writes the chapter plan and skips the publish gate. A real file has to pass the gate.

## What the episode is

| Segment | Job |
|---|---|
| Intro | One exchange. Name the fight. |
| Rounds | Two or more chapters. The aim changes: origin, the money, the chokehold. Every third exchange in a round has to quote an earlier claim. |
| Outro | One exchange. No new topic. Reconcile the contradiction and stop. |

`--target-duration` is seconds, from 300 to 600. The default is 360. The stock 6-minute target uses 20 exchanges. Other targets scale the exchange count between 16 and 32 so the minutes are filled with speech, not silence.

## Pre-publish gate

The package is not publishable unless all of these hold:

- The long file's duration sits in `[max(300, target−120), min(600, target+120)]`. A 6-minute target accepts about 300–480 seconds.
- The long file has an audio stream and is 1920x1080.
- There are 2 or 3 Shorts, and none of them is under 15 seconds. Each one is vertical and has audio.

A failure is printed as `qa : FAIL` and the process exits 1. The metadata records the same failures.

## ffmpeg

Every command in this package goes through `longform/ffmpeg_guard.py`:

- A bare `apad`, or `apad` without `whole_dur`, is refused.
- `amix` with `duration=longest` is refused.
- The process group is killed when the timeout hits (`shared.ffmpeg.run_ffmpeg`).
- Teaser cuts put `-ss` and `-t` before the input, so ffmpeg does not decode the whole episode.
- The fallback plate (used only when the master file is missing) sets `d=` on the color source and `-t` on the output. The generator is not left open.

## Config

All of this lives in `products/aiwake/aiwake_config.yaml` under `long_format`. The loader is `products.aiwake.settings.load_settings`. The operator entry is the shared CLI, which takes the channel as a parameter:

```bash
python tools/channel_cli.py --channel aiwake longform --help
```

`python -m products.aiwake --mode longform --resolution 1920x1080 --target-duration 360` is the same pipeline. The vertical 9:16 shorts flow is unchanged when those flags are absent.
