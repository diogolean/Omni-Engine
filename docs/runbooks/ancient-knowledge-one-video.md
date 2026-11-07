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

## Preserve, then one local render

1. Copy the existing mp4, the QA json, the review json if any, and
   `act01_fallback.jpg` to a folder that this run will not write. Keep the
   YouTube id written down next to them. Do not delete the video on YouTube.
   YouTube will not replace the media of that id. A corrected cut is a new
   upload with a new id, after approval.
2. Render one Ancient Knowledge episode for this subject only. Not
   `--quantity` above 1. Not `--publish-youtube`.
3. Read the new `*.qa.json` and `*.review.json`. `verdict` must be pass,
   and `blockers` must be empty. `semantic_status` will still say
   `needs_human_review`. That is expected.
4. Watch the whole cut. The contact sheet is a map of timestamps, not a
   substitute for watching. Check the opening, the middle beats, the end,
   whether any card is a flat color, whether a picture repeats, whether
   the captions track the voice, and whether the claims are ones you are
   willing to publish.
5. If you accept those bytes, write an approval next to the mp4. The helper
   is `production.qa.acceptance.write_approval`. Pass the approver name and
   the QA report path. Do not approve a file you have not watched. If the
   only blocker was `needs_human_review` (the judge never answered) and the
   picture is otherwise clean, the approval's `acknowledged` list can
   contain that string. It will not waive `static_fallback` or `frame_flat`.
6. Only then is `distribution_ready` true for that path. Uploading is a
   separate decision. This document does not do it.

## Still only on the operator machine

- The QA json, the fallback jpeg, and the mp4 bytes. This repo does not
  contain them, so the green frame is not matched to the jpeg here.
- Whether `lBXQu0jPWgE` is that mp4.
- The new render's sha256, review manifest, and the human watch.
- Word timings from that render, if you want the caption-gap check to see
  the real cue list rather than a summary.
