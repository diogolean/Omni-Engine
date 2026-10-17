# 0006 — Ground reel stills in the episode object

## Status

Accepted

## What was going wrong

Ancient Knowledge scripts were fine. The stills were not, and strict reel QA
(ADR 0005, threshold 0.70, three attempts, fail closed) then blocked the
episode. On the London Hammer reel the judge reported:

- "artefact from London, Texas" → a generic urban alley
- "wood preservation of the handle" → stone structures
- "metallurgical analysis" → a fantasy landscape with ruins and fire
- abstract theory lines often passed, because a vague picture can look like a vague line

Three stages stacked the wrong subject in front of Flux.

1. **The subject pool led the prompt.** `plan_episode_visual_sequence` always
   rotates in "Wide Landscape / Ruins", "Monument from an unusual vantage",
   and "Human explorer", and `main.py` put that sentence first. Flux treated
   "London Hammer" as a place to draw ruins around.

2. **The channel style block asks for megaliths and torchlight**
   (`ATMOSPHERE_STYLE`: rim light on stone blocks, amber fire, volumetric
   haze). It was appended to every beat, including a laboratory line and a
   wooden handle. Sanitize only strips Egyptian landmark words.

3. **Place names were promoted over the object.** `extract_site` turned
   "from London, Texas" into `Site: London`. `refine_prompt` put that site
   in the retry. Flux drew London the city. The same retry was the only
   place a rejected beat got a new prompt, so looping QA could not recover
   a prompt that still described the wrong subject.

A shared assets folder also kept `{topic-slug}_v01_actNN.png` from older
runs of the same topic. QA re-scored those files, so a stale August still
could be the first image the judge saw and pull the first-pass rate down
even after the prompt was fixed.

Super Memory was not reused. It is the aiwake debate ledger
(`channels_config/aiwake/tools/supermemory_bridge.py`): a local server with
a hardcoded WSL path, not something the Oracle ARM image runs. The reel
path keeps a JSONL file instead, under `outputs/<channel>/_review/visual_memory.jsonl`.

## Decision

`modules/reel_grounding` builds the image prompt before Flux.

- The episode brief names the physical object, the materials the script
  actually uses, and the find-spot.
- Each beat leads with `PRIMARY SUBJECT`. A handle line is a close-up of
  that handle. A metallurgy line is the same object on a lab bench. An
  abstract line ("historians debate") still shows the object, not a hall
  or a landscape.
- `City, Region` stays together. When the region is not that city's home
  (London, Texas), the prompt says the famous city is not the picture.
- Shot and light variety still apply, but a wide or torchlit setup cannot
  change an object beat into ruins.
- `REEL_VISUAL_GROUNDING=0` restores the previous prompt. The default is on.
- `REEL_VISUAL_MEMORY=1` (default) appends one row per judged still and
  feeds an accepted depiction or a rejection reason into the next prompt
  for the same topic. Overlap is token Jaccard. No extra model call.

A still in the image cache is reused only when a `.spoken.json` sidecar
says that file already passed QA for the same spoken line. Older files
without a sidecar are generated again.

QA is unchanged for a real mismatch: score under 0.70 still fails closed
and is not replaced with another beat's image. A timeout or an unparseable
judge reply is retried (`REEL_QA_JUDGE_RETRIES`, default 2) on the same
still. If the judge never returns a score, the beat still fails closed.
That retry is not counted as a content miss.

`seal_compiled_reel` writes `first_pass` onto the QA report and prints:

```text
[VisualQA] first_pass_hit_rate=0.78 (7/9)
```

The denominator is freshly generated stills whose first scored verdict
exists. Cache reuse and a judge that never scored are left out.

## Public-domain stills

`agents/media/free_images` already searches Wikimedia Commons, Openverse,
NASA, the Library of Congress, and (with keys) Smithsonian, Pexels, and
Unsplash. Cards stay on `IMAGE_SOURCE_POLICY=free_first`.

Reel beats do not. `REEL_FREE_IMAGES` defaults off. Set it to `1` to search
before Flux, using the beat sentence and the episode object as the query
rather than the style prompt. A miss falls through to Flux. QA still
rejects a photograph that does not match the line.

Europeana needs an API key. The Met Open Access API is two requests per
candidate and mostly returns art objects, which is a weak match for an
out-of-place artefact. Neither was added.

### Cost

The cost tracker bills FLUX.1-schnell at $0.003 per still. Nine beats are
about $0.027 if every still is generated once. The owner's "about $0.01
per episode" is the same band before QA retries; each rejected beat used
to spend another still.

A free-image search adds no LLM call. The keyless providers are plain
HTTP. Smithsonian, Pexels, and Unsplash are skipped when their keys are
unset. The extra cost of `REEL_FREE_IMAGES=1` is latency (a few searches
and at most one download per beat), not a model fee. The vision judge
still runs, so the QA cost does not change. A weak Commons photo that
fails QA then spends the Flux call anyway. That is why the flag is off
until a topic is known to have a usable public-domain photograph.

## Local check

```text
python main.py --page ancient_knowledge --post-type ECONOMIC_REEL --economic --quantity 1 "The London Hammer found in London, Texas"
```

Flux Dev instead of Schnell:

```text
python main.py --page ancient_knowledge --post-type ECONOMIC_REEL --model-api-flow hightier_together_elevenlabs --quantity 1 "The London Hammer found in London, Texas"
```

Read `[VisualQA] first_pass_hit_rate=` in the log, and `first_pass` inside
the `.qa.json` next to the clip. The target is 0.70 or better on the
first scored generation of each beat.
