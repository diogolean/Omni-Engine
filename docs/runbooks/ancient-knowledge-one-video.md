# Ancient Knowledge — one video before any batch

Do not publish, and do not start a batch, until one Ancient Knowledge cut
has been rendered locally, rejected or accepted by the gate in this tree,
and watched by a person. Automatic checks do not establish that the pictures
match the narration or that the script is true.

This run does not render that cut and does not upload.

## What the Dogon QA file already proved

Operator file, not in git:

`outputs/ancient_knowledge/qa/the_sirius_mystery_dogon_4ed43c_v01_v01.qa.json`

Reported contents:

- `verdict=pass`, `publishable=true`
- 7 of 12 beats had `score=0`, a rejection reason, `reuse=fallback`, and `passed=true`
- Beat 1 asset `.../assets/ep_20261104_0353/work/the_sirius_mystery_dogon_4ed43c_v01_act01_fallback.jpg`
- reason `documentary_fail:non_photoreal,anatomy`, 3 attempts, score 0
- MP4 samples only at 0.911s and 78.34s
- first sample `passed=true`, `score=0`, `reason=beat-approved`
- last sample `cta-overlay`

That is a fail-open seal. It does not, by itself, prove the YouTube frame
was the green fallback card. The first beat's file is the fallback jpeg.
Confirming those pixels are the uploaded frame needs the jpeg and the mp4
on the machine that rendered them.

## Where the fail-open was introduced

These are diffs, not a guess from commit order.

- `fbe90b3` ("a hard beat falls back instead of dropping the reel") added
  `_plain_fallback` and set the beat `passed=true` with `reuse=fallback`
  while leaving the rejection and the score on the row. Beat 0 of that card
  is the solid `(86, 108, 72)`.
- `9d84e6f` ("an approved still passes the final frame on presence rather
  than the spoken line") treats `passed=true` plus a matching picture as
  `beat-approved` and does not ask the judge again.
- `fe3f074` ("Make an Ancient Knowledge reel cost about one cent") set
  `FRAME_SAMPLES` to 2. `select_cues` then keeps the first and last cue.
  0.911s and 78.34s are that pair. The last cue was the CTA, so the only
  content sample was the one the shortcut auto-approved.
- `3f94d53` made an unjudgeable frame a warning when `frame_fault` was empty.
  The Dogon row's reason was `beat-approved`, not `unjudgeable`. The same
  `frame_fault` only called a frame black when it was near-black, so a solid
  olive field was not a fault. That hole is closed separately. It is not the
  reason field on this QA file.
- The still-gate sharpness check also misses the card. The pale bar on
  `_plain_fallback` keeps Laplacian variance well above the floor, and a
  short edge is only a note, so `check_still` returns ok. The seal is what
  has to reject `reuse=fallback`.

The package moves (`0a79a2a`, `58764fd`) carry these lines into
`production/`. They are not the edit that started marking fallback beats as
passed. ADR 0005 said a missing judge is a fail and a placeholder is not
substituted. The three diffs above contradict that for this path.

## What this tree does now

- A fallback or held-previous beat cannot seal as pass, even if `passed`
  was forced true and the score is 0.
- A recorded rejection (`documentary_fail`, and the other markers in
  `production/qa/acceptance.py`) cannot be overridden by `passed=true`.
- The final MP4 walk covers the cue list, up to 16 cues, plus an opening
  frame. `frame_samples()` is still 2; that knob is the cheap-judge budget,
  and the final gate does not use it.
- A near-solid frame is `flat`, including the olive fallback. Flat, black,
  unreadable, missing, and repeated stills fail the seal.
- An unjudgeable frame is `needs_human_review`, not a pass. A person can
  acknowledge that one gap. They cannot acknowledge a flat card or a
  rejection.
- `distribution_ready` is what the YouTube path calls. A QA pass is not
  enough. The approval file stores the sha256 of the mp4 and of the QA
  report. Any new bytes make it stale.
- The review manifest (`*.review.json`) lists beats, frame times, and
  blockers. `certifies_truth` is false. A contact sheet is only the frames
  the gate already grabbed.

The compile step can still write a fallback card into the picture so there
is something to watch. That file must not be uploaded.

## The one candidate

Regenerate only this episode, as a new file, after the originals are copied
aside:

| | |
|---|---|
| Channel | `ancient_knowledge` |
| Stem | `the_sirius_mystery_dogon_4ed43c` variant `v01` |
| QA | `outputs/ancient_knowledge/qa/the_sirius_mystery_dogon_4ed43c_v01_v01.qa.json` |
| Work | `assets/ep_20261104_0353` |
| First still | `the_sirius_mystery_dogon_4ed43c_v01_act01_fallback.jpg` |

Master Mei and Wonder Feed are not this candidate. The operator's loop
counted an mp4 with a stream and duration of at least 15 seconds. Those
channels' own logs said `VisualQA verdict=fail`, so they were not the
fail-open pass. Master Mei runs 2 and 6 share
`reel_your_craving_is_a_ghost__starve__v01.mp4`, and runs 5 and 10 share
`reel_your_screen_is_the_cave_wall_v01.mp4`: ten executions, eight paths.
Both names are `_v01`, which is variant 0 of a separate run. The filename
is the first 32 characters of the hook slug. Those two hooks collide, and
ffmpeg is invoked with `-y`, which replaces the file. Durations changing
on the same path fits that overwrite. It is not fixed here. Do not batch
those channels to "see".

The screenshot id `lBXQu0jPWgE` is not in this repo. Do not treat it as the
same file as the QA stem until the QA json's `video_path` on that machine
says so.

## The command

From the repository root, in PowerShell. This is the render entry.
`--dry-run` does not apply: that flag is ECONOMIC_REEL_LOFI only.
Do not pass `--publish-youtube`. The script does not set
`ENABLE_YOUTUBE_UPLOAD`. `--local-review` turns YouTube off even when `.env`
sets that variable, and it skips B2, ImgBB, Post Planner, and the content
library. It does not write an approval file.

```powershell
.\channels_config\ancient_knowledge\tools\Invoke-DogonLocalReview.ps1 `
  -QaJson "<path-to-the_sirius_mystery_dogon_4ed43c_v01_v01.qa.json>" `
  -Mode render
```

The QA json `video_path` must name
`reel_did_an_african_tribe_map_an_invi_v01.mp4`. That file is passed to
`--protect-path`. The topic is the QA `topic` when its slug is
`the_sirius_mystery_dogon_4ed43c`; otherwise the stem itself is the topic.
A different slug aborts before render.

`--local-review` writes under the existing `OUTPUT_PATH` (the output root
the process already uses, including a G: root):

```text
<OUTPUT_PATH>/ancient_knowledge/clips/local_review/<UTC-stamp>/
  reel_<hook>_v01.mp4
  reel_<hook>_v01.qa.json
  reel_<hook>_v01.review.json
  assets/ep_<new>/work/
```

The first lines of the run print `output=` that directory. Nothing there
replaces `clips/reel_did_an_african_tribe_map_an_invi_v01.mp4` or
`assets/ep_20261104_0353`.

Each still is copied onto the machine's local temp disk before ffmpeg.
A read that does not finish in 30 seconds fails that beat. One beat's
encode budget is about 20 seconds plus 0.2 seconds per frame, and never
more than 120 seconds or `REEL_FFMPEG_TIMEOUT_S`, whichever is smaller.
A flat or black card is not encoded. A missing act image is not filled
with a near-black PNG (the old `(10, 10, 14)` placeholder, which the
encoder reports as `refusing to encode a black still`). On this channel,
a retry after a blank frame, a modern person, an anachronism, or a
non-photo asks for an allowed place photograph. The spoken line stays
context. The judge threshold is unchanged. A still is reused from an
earlier `local_review` folder only when its sidecar says that exact
spoken line already passed, and the judge scores the copy again. A
static fallback card is not a scene and is not a pass.

After the render, sample the new mp4 without another render:

```powershell
.\channels_config\ancient_knowledge\tools\Invoke-DogonLocalReview.ps1 `
  -QaJson "<same-qa-json>" `
  -Mode sample `
  -Video "<OUTPUT_PATH>\ancient_knowledge\clips\local_review\<stamp>\reel_<hook>_v01.mp4"
```

The sample manifest is
`<video-directory>\local_review_samples\<stem>.sample.json`. It records flat,
black, and unreadable frames across the timeline. It does not judge the
narration or the facts, and it does not write `.approval.json`.

Read the new `*.qa.json` in the stamp directory. `blockers` must be empty
before anyone treats the cut as ready to watch for approval.
`semantic_status` remains `needs_human_review`. Watch the whole file.
Approval is `production.qa.acceptance.write_approval` on the new mp4 only,
later, and this command does not do that.

## Preserve, then one local render

1. Originals stay where they are. The command refuses to write the protected
   mp4 and the QA json path. Do not delete the YouTube video. YouTube will
   not replace the media of that id.
2. Run the PowerShell command once. Not `--quantity` above 1.
3. Read the new `*.qa.json` and `*.review.json` in the stamp directory.
4. Run the sample command on the new mp4.
5. Watch the whole cut before any approval.

## Still only on the operator machine

- The QA json, the fallback jpeg, and the mp4 bytes. This repo does not
  contain them, so the green frame is not matched to the jpeg here.
- Whether `lBXQu0jPWgE` is that mp4.
- The new render's sha256, review manifest, and the human watch.
- Word timings from that render, if you want the caption-gap check to see
  the real cue list rather than a summary.
