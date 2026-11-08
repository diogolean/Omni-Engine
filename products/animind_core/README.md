# Animind core API (contract v2)

Small HTTP API that the Animind app calls at `ANIMIND_CORE_URL/v1/*` (see
`lib/contract.ts` in github.com/diogolean/animind). URL prefix `v1`, payloads
follow contract **v2** (views `front`/`left`/`right`, each with layers + 9
Rhubarb visemes).

| Endpoint | What runs |
| --- | --- |
| `POST /v1/characters` | `type: "rig"` uses `rig.py` when Gemini is configured: Gemini master via Aiwake `create_puppet.generate_character_image` -> generated mouthless head + 4x3 parts sheet (nine Rhubarb mouths and three lid states) -> BiRefNet matte -> registered layers. Together or OpenRouter rigs, and every stick figure with a key, use `avatar.create_avatar`. `type: "stick"` with no image key uses `stick.py` as the offline mock. |
| `POST /v1/render`, `GET /v1/jobs/{id}` | edge-tts per line (retried on `NoAudioReceived`) -> Rhubarb visemes (`animation.parametric.rhubarb`, envelope fallback) -> hard-cut mouth sprites in `scene.py` -> Rigly/ffmpeg MP4. After encoding, `frameqa.py` decodes every frame. A bad mouth layer is replaced with the safe drawn set and the shot is encoded once more. If a video file exists, the job is `done` and the QA note is in `warnings` (and `frameQa.warnings`); the job fails only when there is no video. Results include `frameQa` and `frameQaUrl`. `X-Animind-User` is an opaque id (an email is refused) and owns the job; another account, or a request without that header, gets the same 404 as a missing job. |
| `POST /v1/preview` | Lightweight PNG before the full rig. Body: `{character: {name, prompt}, characterType: "stick"\|"illustrated", artStyle, language, index}`. Response: `{id, index, name, kind, source: "core", accent, imageUrl, note}`. This is not the final cut-out; that is still `POST /v1/characters`. |
| `POST /v1/scenarios` | `{mode: "auto", characterNames}` or `{mode: "prompt", prompt, characterNames}` -> `{scenario, source: "core", prompt?, note?}`. `scenario` is one of `studio`, `classroom`, `arena`, `space`, `agora` (classical names map to `agora`). A free-text prompt is mapped to the nearest built-in plate; the core does not paint a new set. |
| `GET /v1/files/{path}` | Generated PNG/JSON/media, confined to `ANIMIND_DATA_DIR`. A render with `X-Animind-User` is stored under `users/<hash>/jobs/<id>` and is not served to anyone else. |
| `POST /v1/debates`, `GET /v1/debates/{id}` | Two to six persona names, a theme, and `pt` or `en`. The Aiwake debate room takes one turn per persona. Paid generation stays off, so the seated model is the fixture stub and the response says `writer: "fixture"` and `llm.live: false`. A line is a sheet quote only when that quote is primary, on the theme, and verified; otherwise the line is `kind: "fixture"` with `text: null`. The same opaque `X-Animind-User` owns the script. Another account, or a request without that header, gets the same 404 as a missing debate. The JSON file is `users/<hash>/debates/<id>.json`. |

### Telemetry and provider comparison

Every script, avatar build and render appends one secret-free JSON object to
`ANIMIND_DATA_DIR/telemetry/events.jsonl`. Rows include provider/model, text
tokens, image count, USD, wall latency, script/image/frame QA summaries and
the local artifact path. Prompts, generated text and key values are never
written to telemetry. Unit/integration runs set `ANIMIND_DATA_DIR` to their
temporary test folder, so test telemetry also stays outside git.

Provider calls made while a request is in flight are also on the `usage` field
of `POST /v1/plan`, `POST /v1/render`, and `GET /v1/jobs/{id}`. The field is
`animind.usage.v1` (see `docs/spend-contract.md`). Null estimated or reported
cost means unknown. Job polls and file downloads do not open a new reservation.

```bash
# Aggregate all rows by operation/provider/model as a Markdown table.
python -m products.animind_core.telemetry summary

# Run only configured providers; missing keys become explicit dry-run rows.
# Paid image work has an absolute USD 0.30 ceiling.
python -m products.animind_core.benchmark \
  --output ~/.animind-core/benchmarks/latest --image-budget-usd 0.30
```

The benchmark compares direct DeepSeek, Gemini Flash-Lite, and configured
DeepInfra/OpenRouter cheap models. For image models it records every registry
entry; unconfigured or not-yet-enabled Qwen/Flux adapters are dry runs with
the missing key/adapter named. `--dry-run-images` disables paid image calls
even when a key exists.

Phase 5 runs the same English monologue, the same English debate, and the same
stick-figure character through every named provider. Images and the Markdown
table go to `ANIMIND_DATA_DIR` (never the git checkout). The default run cap
is US$0.10 (`ANIMIND_RUN_BUDGET_USD`). Without keys the report says real
results were not produced.

```bash
python -m products.animind_core.bench \
  --providers deepseek,gemini,openrouter,together-qwen-image,together-flux-kontext
```

`create_avatar(description, style)` in `avatar.py` is the one-call sheet.
The default image model is Together `Qwen/Qwen-Image`. Only
`TOGETHER_QWEN_IMAGE_MODEL` overrides it. `TOGETHER_IMAGE_MODEL` is ignored,
because an owner's FLUX.1-schnell value makes Together return 400. The front
portrait is generated first. The side view is a `FLUX.1-kontext-pro` edit of
that portrait; if the edit fails the profile is missing and the front picture
is not reused as a side view. Skin is then matched to the front. Gemini still
draws through Aiwake `create_puppet.generate_character_image`. FLUX.1-schnell
is not called. Face parts are cut by `rig.assemble`
so the mouth stays inside the face. `southpark` is its own style. What is reused is mapped in `docs/aiwake-reuse.md`. The bust is cached
under `ANIMIND_DATA_DIR/avatar_cache/` and reused for every scene. `stick.py`
draws only when no image provider is configured.

**Rig reuse:** a prompt character whose name (accent/case-insensitive) and art style match a saved,
QA-approved (score >= 80) Gemini rig returns that rig (`"reused": true`, URLs rebased) instead of
generating a new image. Send `"reuse": false` (or a `rawPrompt`) to force a new one.

Without a Gemini key (or with `ANIMIND_FORCE_MOCK=1`) characters are a flat PIL
drawing cut into the same layers; the response says `"generator": "mock"`.
The same happens when Gemini answers 402 / depleted credit / bad key: the request still returns 200
with mock art and `generatorNote` ("Gemini indisponível: ..."), and from then on this process stops
calling Gemini (`/v1/health` reports `generator: mock` plus the same `generatorNote`). Other build
errors still fail.

Stick characters are `"generator": "procedural"` only when no image provider is configured,
and then they cost zero. With a Gemini, Together, or OpenRouter key they are
`create_avatar` image sheets. The render endpoint accepts `solo`/`monologue`, the existing `debate`, and
`dialogue`/`dialogo` with every character in the supplied array participating. App controls
`tone` (`humor`, `terror`, `educacional`, `sabio`, `serio`, plus the legacy ids), `objective`
(`ensinar`, `debater`, `entreter`, `convencer`) and `duration` (10–180 seconds target) are passed to
both writers. The offline template makes tone and objective visible in its hook rather than merely
returning them as metadata. `GET /v1/tones` and `GET /v1/objectives` expose the catalogs.

`language` is the canonical BCP-47 render field (`lang` remains an alias). It defaults to `en-US`;
`pt`/`pt-BR` select Brazilian Portuguese. The locale controls the offline script bank, Edge voice
pool, titles/caption labels and disclaimer. Other BCP-47 locales remain valid: Edge's catalogue is
resolved at runtime (or explicit `voices[]` may be supplied). They never fall back to English:
until a rich locale bank is registered, the offline writer uses a language-neutral shape containing
only the caller's localized topic, and untranslated UI chips are omitted. Thus speech and captions
stay in the selected language without Gemini or a translation API. `GET /v1/languages` exposes the
two rich built-in banks; the accepted locale set comes from Edge.

Voice casting uses the character's inferred `gender` (`male` or `female`), from an explicit field,
persona wording, Wikidata or the first name, and returns it in both the character and render
contracts. Automatic picks always come from that gender's pool and speakers receive distinct voices
when the language pool has capacity. A character-level `voiceId` overrides the automatic pick
(`voices[]` remains compatible); render results expose both canonical `voiceId` and legacy `voice`.
`GET /v1/voices?lang=en` filters one locale. Unfiltered `GET /v1/voices` returns the complete Edge
catalogue with `voiceId`, gender, BCP-47 locale and the locale name in its own language.

Rhubarb selection is language-aware. PocketSphinx is used only for English; all other locales use
`phonetic`. The phase-2 comparison used identical edge-tts clips and dialog text with Rhubarb
1.13.0: English measured 30 cues / 6 non-rest shapes with PocketSphinx versus 26 / 6 with phonetic;
pt-BR measured 41 cues / 8 shapes with phonetic versus 32 / 5 with PocketSphinx. Thus phonetic
preserved substantially more Portuguese articulation, while PocketSphinx gave denser English cues.
The proof bundle includes `recognizer/measurement.json`.

Stick characters have four deterministic presets (`teacher`, `sage`, `rebel`, `explorer`) exposed
by `GET /v1/stick-presets`. `palette.skin`, `palette.outfit` (or `clothing.color`) and `accessory`
are overridable. An identical name+preset+customization resolves to the same cached character and
returns `reused: true`, without image generation or Gemini.

The mouth family comes directly from Aiwake's
`products/aiwake/animator/asset_generator.py::_draw_ghibli_mouth_patch`: organic X/A-H visemes,
`smug_smile` (the asymmetric triangular grin) and `stressed_grimace`. It is shared by stick rigs
and complex rigs. Seven emotional rest variants are stored separately from visemes, and the
renderer substitutes one only during X/rest frames, preserving exactly one mouth layer per frame.
Every viseme and emotional mouth is flattened to a maximum painted height/width of 0.48, and
frame QA enforces an independent 0.50 cavity ceiling; a tall reference sprite cannot raise it.
The generated `review_sheet.png` now includes all mouths, eye states/directions, emotional brows
and expressions for all three views; it stays under `ANIMIND_DATA_DIR`, outside git.

`/v1/health` also lists `scenarios`, `templates`, and the contract-only future
`illustrated-scene` mode this process understands. That contract accepts a plate image plus
normalized face anchors/scales for eye and mouth overlays, but performs no image generation.

## Scripts: real quotes, marked paraphrases

`script.py` writes every render from a beat plan, and `personas.py` keeps a sheet of real quotes per
character (Wikiquote MediaWiki API, cached as YAML in `ANIMIND_DATA_DIR/personas/<lang>/`, outside git;
"not found" is cached for 7 days; sheets from an older `SHEET_VERSION` are rebuilt on the next load).

- Sheet quotes carry `sourceKind`: `primary` (the source names a work by or about the person: book,
  dialogue, letter, speech, page or §) or `secondary` (magazine, website, anthology, "citado em", no
  work at all, or an index/register page such as "Sachregister" or "Índice", which only lists where words
  occur; `is_primary` also rejects those on sheets cached before `SHEET_VERSION` 3). Entries in "Atribuídas/Disputed/Misattributed/Sobre/Quotes about" sections, or whose
  source line says "atribuída/disputada", are dropped. Up to 24 quotes, primary first, short (6–20 words)
  first, one per work per round so every book gets a chance.

- **debate** (four acts, strict A/B alternation): *abertura* gancho (hook, to camera; if none of B's quotes is
  on the topic it sets one up: "aposto que você vai fugir pro assunto X") -> tese (B quote) -> *confronto*
  réplica -> réplica (short jabs) -> *virada* (A quote, to camera, the turn in the middle) -> réplica -> tréplica ->
  *fechamento* fechamento (B) -> fechamento (A's strongest remaining quote that answers the last line, else
  written). Every line must answer the one before (`link`: a shared content stem or concept group, never just a
  name or a mold word; each line carries `act` and `replyTo {line, idea}`); a mold is preferred when the next
  speaker's quote can answer it (set-up/payoff). LLM lines that ignore the previous line, or a hook off the
  topic, are rewritten from molds. `check_debate` (part of `scriptQA`, below) flags
  monologues, unlinked lines, a missing hook/turn/close, a speaker without a real quote, written lines over 16
  words (quotes over 30), no jab of <= 9 words and monotonous lengths.
- **lesson** (acts *abertura* -> *desenvolvimento* -> *virada* -> *fechamento*): gancho (<= 14 words, names the
  topic) -> pergunta (a real question) -> citação (quote on the topic) -> exemplo (an everyday case built on a word
  of that quote) -> virada (a second, punchier quote, only if it stays on the thread: it shares an idea with the
  line before or the topic; otherwise a written turn) -> síntese (<= 16 words, recalls the topic or a quote).
  A beat named "citação" is always a real quote: a written fallback is relabelled "ideia".
  **solo**: gancho -> tese (quote) -> argumento -> fechamento.
- **scriptQA** (`{pass, problems, notes}` in every job result, all templates): `check` (quote fidelity),
  plus `check_debate` or `check_lesson` (shape and rhythm). Fidelity: a `quote` line must be a sheet quote
  byte for byte (same id and text), keep the sheet's `source` (non-empty) and Wikiquote/Wikipedia/Wikisource
  `url`, and be primary; anything else is reported as *paraphrased quote shown as literal* (it reuses half or
  more of a sheet quote's words, `closest_quote`) or *invented quote*. A `paraphrase` line may not use quote
  marks, claim literal wording ("como eu escrevi:", "nas palavras de X,", "palavra por palavra", "in his own
  words,", "Em <obra>:"), repeat 7+ words of a real quote, or sit on a "citação" beat. LLM lines that claim
  literal wording are rewritten from molds. Shape: a strong hook (<= 14 words, a question or a punch of <= 4
  words) on the topic; a memorable close (a quote with punch or <= 14 words, or a written line <= 16 words);
  `notes` lists softer issues (the same idea picked up three lines in a row).
- `GET /v1/personas/sheet?name=&lang=` returns the cached sheet (id, text, source, work, sourceKind, url,
  wikisource, verification, sourceSite). `verification` is `verified` only when that text was found on the
  Wikisource, Wikipedia, or Wikiquote page. `not_found` is kept on the sheet and is not spoken as a real line.
  `X-Animind-User` stores the sheet under that user's hash; an email is refused. The same header on
  `POST /v1/render` makes the debate read that user's sheet.
- Rhythm: each beat has a word budget (`maxS` = words / (2.6 × pace) + 0.8 s), a pause after it (0–0.6 s: longer
  after a hook, a quote or the turn, short between quick jabs) and a pace nudge `rateAdj` (quotes −5…−8 %, jabs
  +2…+6 %) added to the cast voice's rate. A clip that overruns `maxS` is voiced again at `max(rateAdj, 0) + 15%`
  (`ttsRate` keeps what was used). Each clip is tightened first: edge-tts' ~0.9 s silences after "."/"?"
  are cut to 0.38 s and lead/tail silence to 0.06 s (`render.tighten`), so short jabs do not drag. Offline (no LLM) lines come from several molds per beat, rotated by a seed of
  the topic and cast; no mold, and no picked-up word, repeats inside one script.
- `kind: "quote"`: the text is copied by code from the sheet, byte for byte, with
  `quote {id, text, source, label, url, site, wikisource?}` (`label` is the short on-screen source: no
  page numbers, translators or publishers, <= 48 chars; `source` stays complete).   The pluggable
  script writer uses DeepSeek `deepseek-flash` by default and Gemini
  (`gemini-3.5-flash-lite`) as fallback. Gemini 3.5 Flash-Lite is called with
  `thinking_level=minimal` (budget 0 is a 400 on that model). Answers may be
  `{"lines":[...]}` or a bare JSON array. Either provider only picks
  quote ids and writes the other lines. Only `primary` quotes can be a "Citação real". Quote beats prefer
  quotes sharing words with the topic (stemmed): an unknown/reused id, or an off-topic id while an on-topic
  quote fits, becomes the best topic match (then the shortest), never model text.
- A written line that is one of the speaker's primary quotes (same words after normalizing case, accents
  and punctuation, or contains it, or a >= 6-word excerpt covering 60%+ of it) is shown as that quote, with
  the sheet's literal text and source.
- `kind: "paraphrase"`: written lines, never shown as quotes. Quote marks are removed, and a line that
  repeats 7+ words of a quote is replaced. `basis` names the quote it restates, if any. With no sheet
  (fictional characters, offline) every line is a paraphrase.
- On screen: a chip "CITAÇÃO REAL · source · Wikiquote" or "PARÁFRASE"; quote captions in italics
  within “ ”; captions paged two rows at a time. The lesson board shows the beat, the line set as large as the board allows, and the quote source; captions carry a tracked speaker label.
- Film titles (`titles.py`): render leaves 1.8 s of silence before the first line, 0.7 s before each new act
  and 2.4 s after the last line. A cold-open title card (kind, names, topic in EB Garamond Italic) sits in
  the lower band under a 2.39:1 matte that eases off as the voices start; each act break dips the picture
  to 32% (never black), hard-cuts at the bottom of the dip and shows a chapter mark (“II — O CONFRONTO”) top
  left; an end card and the matte return after the last line. 9:16 keeps the cards but no matte. In wide
  and two shots the vignette and a warm key glow lean toward whoever has the floor.
- Debate camera edits are deterministic and speech-paced: an establishing wide leads into alternating
  over-shoulders/speaker singles, quote close-ups, listener reaction inserts, context two-shots and a
  closing two-shot. `cameraPlan` exposes the complete edit decision list in the render result.
- Each quote beat ranks the speaker's quotes by topic fit (shared stems plus related concepts) and by
  overlap with the line before it, so a quote answers the previous argument. Every line goes through
  `tidy()` (no space before punctuation, no "?." or "..", capital first letter, final mark).
- Without Gemini (no key, no credit, or `use_llm=False`) the template writer still uses the sheet quotes and
  writes short replies that pick up a noun from the previous line and name the other speaker
  (hook -> exchange -> climax -> close); no line repeats. Gemini and DeepSeek answers are cached
  on disk by provider, model, QA rule version and prompt (`ANIMIND_DATA_DIR/cache/script/`).
  A cache hit is logged at cost 0 with `cached: true`. A draft that fails the current QA rules
  is deleted and is not reused. Quote ids in that prompt are `speaker:qN` (`albert_einstein:q1`),
  because every sheet numbers its quotes `q1`, `q2`, …. The prompt text is part of the cache key,
  so scripts cached before that prefix are not reused.
- Result adds `writer` (`deepseek`|`gemini`|`template`), `scriptCost` (provider, model, tokens, cache hit and USD for this
  script), `quoteLines`, `topic` and `personas` (sheet link, quote count, CC BY-SA 4.0).
  The character name is cut before a comma or parenthesis to find the person ("Sócrates, de toga" -> "Sócrates").

## Env

- `CORE_API_KEY` (required; `Authorization: Bearer ...`).
- `DEEPSEEK_API_KEY`: default script writer. `ANIMIND_SCRIPT_PROVIDER=gemini` reverses the
  DeepSeek/Gemini fallback order; `DEEPSEEK_BASE_URL` and `DEEPSEEK_MODEL` are optional.
  DeepSeek cost uses the official peak tariff (the conservative upper bound): cache miss
  `$0.30/M` input, cache hit `$0.006/M`, output `$1.20/M`, checked 2026-11-02 at
  [DeepSeek Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing/).
- `GEMINI_API_KEY` (script fallback; absent -> mock art). `ANIMIND_DATA_DIR` (default
  `~/.animind-core`, outside git). `CORE_PUBLIC_URL`.
- `OUTPUT_PATH` / `OUTPUTS_DIR`: shared cost ledgers. When unset, those files go to
  `~/.cache/omni-engine/outputs` (`XDG_CACHE_HOME` overrides the cache root), not
  `<repo>/outputs`. The repo `outputs/` directory stays gitignored.
- `ANIMIND_PERSONAS_OFFLINE=1`: never fetch Wikiquote (tests); only cached sheets are used.
- `RHUBARB_PATH`: Rhubarb Lip Sync binary (Linux has no auto-download; absent -> envelope visemes:
  `render.envelope_visemes` picks one mouth per ~90 ms syllable window by loudness and rotates shapes, so
  the mouth still talks; `visemeSource` in the result says `envelope`).
- Cost: one character is one budget unit (both caps are reset per request). A character is 3 images
  (~$0.04 each) + one text call. The engine defaults (`REEL_REEL_BUDGET_USD` $0.05 and the
  Google guardrail `MAX_GOOGLE_COST_PER_RUN_USD`, clamped to $0.05) only fit the base image: the
  mouth shots then fall back to warps of the closed mouth (`mouthShots` says so). For the full set:
  `REEL_REEL_BUDGET_USD=0.15 MAX_GOOGLE_COST_PER_RUN_USD=0.15 GOOGLE_COST_ALLOW_ABOVE_CEILING=1`.
- Memory: BiRefNet on CPU peaks near 8 GB while a character is cut; it is freed after each request,
  and characters are built one at a time.

```bash
pip install -r requirements.txt -r products/animind_core/requirements.txt
CORE_API_KEY=... RHUBARB_PATH=/path/to/rhubarb uvicorn products.animind_core.app:app --port 8787
pytest products/animind_core/tests
```

## End-to-end over HTTP (opt-in)

`tests/test_e2e_http.py` runs persona sheet -> script -> rig -> render against a real core (spawns
uvicorn on a free port with a temp data dir, or uses `ANIMIND_E2E_CORE_URL`/`ANIMIND_E2E_CORE_KEY`)
and checks the MP4 with ffprobe (video + audio, 1280x720, >15 s) and that every quote line is on the
live Wikiquote page. Skips cleanly without network (Wikiquote, Edge TTS) or ffmpeg; the Gemini art
check skips without `GEMINI_API_KEY` (`ANIMIND_E2E_MOCK_ART=1` forces mock art). ~2-6 min.

```bash
ANIMIND_E2E=1 RHUBARB_PATH=/path/to/rhubarb pytest products/animind_core/tests/test_e2e_http.py -v -rs
```

## Persona modes: quick vs deep

`personaMode` on `POST /v1/render` (`"quick"` default, or `"deep"`), implemented in `knowledge.py`:

- **quick**: a few key terms. The index holds the Wikiquote sheet plus the Wikipedia intro, up to 40 chunks. It is built inline on first use and cached next to the persona sheet (`personas/<lang>/<slug>.quick.json/.npy`).
- **deep**: a background job builds a per-character index from the full Wikipedia article, the cleaned Wikiquote page and up to 6 Wikisource texts, up to 600 chunks. It is cached as `personas/<lang>/<slug>.deep.*`. If no deep index is ready when a render asks for one, the render uses quick.
  - `POST /v1/personas/deep {name, lang}` starts the job, or joins one already running.
  - `GET /v1/personas/deep?name=&lang=` returns `ready | building | failed | missing`, with the chunk count and the source counts.
  - `GET /v1/personas/jobs/{id}` returns the job.

Chunks are about 110 words with a 25-word overlap. They are embedded locally with hashed stem and bigram vectors (numpy, 1024 dimensions), so there are no API calls and no extra Gemini cost. For each speaker, the top passages for the topic go into the script prompt as background notes only. Quotes still come only from sheet ids, verbatim. Written lines that copy 7 or more consecutive words from a passage are rejected. `result.personas[]` reports `mode`, `chunks` and the `passages` used (source, url and score).

## Art styles and the front view

`GET /v1/styles` lists `cartoon, anime, retro, realista, cyberpunk, aquarela` (`styles.py`).
`POST /v1/characters {"artStyle": "anime", ...}` puts the style into the Gemini prompt; render takes
`style` (default: first character's `artStyle`) and applies a scene look on the same sets: plate
tint, LUT, saturation and paper/scanline texture. No extra Gemini calls per style.

Each Gemini rig also gets a real front view: a second image generated with the side view as
reference (nose centred, both ears visible), cut and rigged like the side (`views/front`,
`character_front.png`, `frontImageUrl`). Camera-address beats (`gancho`, `fechamento`, `resumo`)
use it; the review sheet shows side and front with the style label. Budget: see `.env.example`.

Front views are checked on the detector boxes (`rig.frontality`): eyes and mouth must sit within 4%
of the face width from the face centre and both eyes match in width (3/4 art measures >= 6.5%). A
3/4 result is redrawn once with a firmer prompt, before the mouth edits, so the retry stays inside
the Gemini cap (the open mouth then falls back to a warp). `POST /v1/characters/{id}/front` redraws
only the front of an existing character. In 9:16, singles put faces at 34% height and close-ups at
29%, so chins stay above the captions (70%).

### Rig layers (v4)
Each view (front/left/right) is `torso` → `neck` → `head` (mouthless: the mouth region is generated
as smooth skin, with inpaint only as fallback) → eyes (`eye_white`, `iris`, lids) → `brows` → mouth viseme (`mouths/X..H`, its own
layer on top). The bust is sliced with the Aiwake robots' slicer
(`animation.parametric.factory.layer_slicer.slice_character_layers`: head ends at the jaw, the body
owns the neck, which rises behind the jaw). The face no longer rides the jaw: only the mouth layer
changes with speech; torso/neck follow 40%/70% of the breath. Partial openings scale only the open
cavity (pixels that differ from the closed mouth) about its top edge, so mustaches and lips are
never squashed into a second copy (the old "tear" in close-ups).

## One-click character: generate-then-verify

The response adds `eyeControl` (`animind.eyeControl.v1`) and `eyeControlReady`.
A supplied view is `gaze: "separable"` only after the pupil centroid moves
left, right, up, and down while pixels outside the socket stay put. A view
that was not drawn is omitted. `eyeRig.maxShift` stays the published travel;
the proof uses the web's `[8, 4]` fit. See `docs/eye-control.md`.

`POST /v1/characters` (one call) builds a rigged character:

1. **Master** (Gemini nano-banana, closed mouth, 4:5).
2. **Parts sheet** (4x3: 9 mouths A–H/X + eyes open/half/closed). The grid is auto-detected (4x3, 4x4,
   white or magenta background); each cell is affine-registered on the master and kept only if its NCC
   >= 0.7. At least 5 good mouth cells accept the sheet; bad cells fall back to warped mouths.
3. **Front view** (side as reference), gated for frontality (eyes/mouth offset, eye ratio) and for
   landmark drift vs. the side art. The front rig also uses the sheet mouths whose cells register.
4. **Retries:** a failed sheet/front gate is re-drawn (up to 3 per asset, firmer prompts) while the
   Gemini cap (`MAX_GOOGLE_COST_PER_RUN_USD`, `REEL_REEL_BUDGET_USD`) has room for another image.
5. **QA** (`qa.py`): skin deltaE, residual/double mouth, mouth opening, front symmetry, landmarks,
   alpha edges and resolution -> score 0–100. `qa.json` + `qa_sheet.png` are stored with the character; a failed
   skin/edge gate on sheet mouths rebuilds with deterministic mouths (free).
6. **Render QA** (`frameqa.py`): the encoded MP4 is decoded frame by frame. A viseme that drew shut
   (the usual moustache miss: `open 0.09 vs drawn 0.00`) is replaced with the safe mouth set and the
   shot is encoded once more. The job fails only when no video file exists; otherwise `warnings` on
   the job carries the QA note. The trace also carries
   one `speech` span per voiced line: a speaker whose drawn mouth never changes shape during a line
   (>= 0.4 s), whose measured opening never reaches `OPEN_MIN`, or a video with speech and `maxOpen` 0
   fails too (a frozen mouth is not a pass).
   The same trace records the actual lid and brow pose per character: clips of at least six seconds
   must blink at 8–36 starts/minute, an emotional line must leave the neutral brow pose, and multiple
   emotions must produce multiple brow poses. The result reports `blinkRatesPerMin`,
   `browEmotions`, and `browPoseCounts`.

**Cost:** $0.039/image. No retries = 3 images = ~$0.12. Test-phase cap 0.30 allows up to ~7 images;
production should use a lower cap (see `.env.example`).

**Image models:** `image_models.py` defines one provider boundary and config-only adapters for
Gemini, Qwen Image through DashScope, and Flux through fal.ai. Phase 4 never invokes those adapters:
their `generate()` method fails closed with `PaidImageGenerationDisabled`; the existing rig path is
unchanged. Selection uses `ANIMIND_IMAGE_MODEL` / `ANIMIND_IMAGE_MODELS`, and the monthly ledger
(`ANIMIND_FREE_CREDITS`) only estimates usage. Keys are read from env by name and never returned.

List prices checked 2026-11-02 (USD; promotions and input-image charges excluded):

| Registry id | Gateway/model | Price represented by `usdPerImage` | Official source |
| --- | --- | --- | --- |
| `nano-banana` | Google `gemini-2.5-flash-image` | $0.039 / image up to 1024² | [Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing) |
| `qwen-image` | DashScope `qwen-image-3.0`, Singapore | $0.030 / successful 1K or 2K output | [Alibaba Model Studio pricing](https://www.alibabacloud.com/help/en/model-studio/model-pricing) |
| `flux-pro` | fal.ai `fal-ai/flux-pro/v1.1` | $0.040 / output MP, rounded up | [fal.ai model pricing](https://fal.ai/models/fal-ai/flux-pro/v1.1) |
| `flux-kontext` | fal.ai `fal-ai/flux-2/edit` | $0.012 / processed MP | [fal.ai pricing](https://fal.ai/pricing) |

The three Phase-4 hero stick presets (`teacher`, `sage`, `rebel`) use a short neck behind the jaw,
separate head/torso layers, broad grounded shoulders and medium-bust framing adapted from
`products/aiwake/animator/factory/create_puppet.py` and
`products/aiwake/tests/test_side_by_side_parity.py`. Their tapered, variable-thickness brow sprites
adapt the shape vocabulary in `products/aiwake/animator/render/facial_rig.py`
(`_tapered_arch_brow`, `_straight_blade_brow`, expression lift/rotation), while Animind's renderer
continues to animate each brow independently. `explorer` remains as a compatibility preset.

The complete avatar path is inherited from Aiwake rather than duplicated:

- `products/aiwake/animator/factory/create_puppet.py`: source portrait and
  numeric bust framing;
- `products/aiwake/animator/factory/one_shot_factory.py`: canonical avatar
  contract and reusable factory decisions;
- `animation/parametric/factory/layer_slicer.py`: overlapping torso, neck and
  head split;
- `products/aiwake/animator/render/facial_rig.py`: eyes, lids, delicate brow
  shapes and emotion transforms;
- `products/aiwake/animator/asset_generator.py`: South-Park-style X/A–H and
  emotional mouth family, consumed through `mouthset.py`.

Each character response now identifies those sources in `avatarPipeline` and
includes `viewsContactSheetUrl`. The sheet is composed from the actual layered
`front`, `left` and `right` views. Right is produced by mirroring every left
sprite and anchor—including eyes, lids, brows, all nine visemes, emotional
mouths, `mouthQA` boxes and iris centres—not by mirroring only the torso.
