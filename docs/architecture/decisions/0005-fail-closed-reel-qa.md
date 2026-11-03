# ADR 0005 — Reel QA fails closed

## Status

Accepted

## Context

Facebook viewers reported stills that do not match the narration. The spoken-line judge in `modules/reel_visual_qa.py` treated a missing OpenRouter key, a down provider, or an unparseable reply as a pass. After one retry it copied an unrelated cached still or wrote a black frame. Only `ECONOMIC_REEL` was checked. The finished MP4 was not checked, so a silent reel could ship. One still often covered a whole act.

## Decision

A reel is publishable only when a QA report says `verdict=pass`.

1. If the judge cannot run, the verdict is fail. The provider order is `REEL_QA_MODEL_CHAIN` (default `qwen,gemini`, Gemini Flash).
2. A rejected still is retried up to `REEL_QA_MAX_ATTEMPTS` (default 3). Each retry names the narration sentence and any proper noun or site. Unrelated cached stills and black placeholders are not substituted. The last failure blocks publish and is logged.
3. Narration is cut into sentence groups no longer than `REEL_QA_MAX_SECONDS_PER_IMAGE` (default 5s, operating band 4–6s). One still per group.
4. The MP4 is sampled at subtitle times and scored against that subtitle. Audio must be present and loud enough (`REEL_QA_MIN_MEAN_VOLUME_DB`, default −45 dB mean). Duration must be at least `REEL_QA_MIN_DURATION_S` (default 15s).
5. The report JSON (per-beat scores and the final verdict) is written next to the MP4 and under `outputs/<page>/qa/`.
6. Post Planner and YouTube call `modules.reel_qa.publish`. They publish only `verdict=pass`. This applies to `ECONOMIC_REEL`, `WAN_REEL`, `ECONOMIC_REEL_LOFI`, and `REFERENCE_BASED_REELS`.

## Consequences

- A missing API key stops the reel from entering Post Planner. It does not ship a mismatched or silent file.
- Image spend can rise because a beat may be redrawn a few times and because acts are shorter.
- Operators read `*.qa.json` beside the clip to see which sentence failed.
