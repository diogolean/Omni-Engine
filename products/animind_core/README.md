# Animind core API (contract v2)

Small HTTP API that the Animind app calls at `ANIMIND_CORE_URL/v1/*` (see
`lib/contract.ts` in github.com/diogolean/animind). URL prefix `v1`, payloads
follow contract **v2** (views `front`/`left`/`right`, each with layers + 9
Rhubarb visemes).

| Endpoint | What runs |
| --- | --- |
| `POST /v1/characters` | `rig.py`: Gemini master via Aiwake `create_puppet.generate_character_image` -> generated mouthless head + 4x3 parts sheet (nine Rhubarb mouths and three lid states) -> BiRefNet matte -> registered layers `torso`, `neck`, mouthless `head`, eyes/lids, `brows` and transparent mouth sprites `X A B C D E F G H`. A separate referenced Gemini shot supplies the frontal view; `right` mirrors the 3/4 `left` view. |
| `POST /v1/render`, `GET /v1/jobs/{id}` | edge-tts per line -> Rhubarb visemes (`animation.parametric.rhubarb`, envelope fallback) -> hard-cut mouth sprites in `scene.py` -> Rigly/ffmpeg MP4. After encoding, `frameqa.py` decodes every frame and fails the job if more than one mouth layer, a stacked second mouth, or an excessive opening is found. Successful results include `frameQa` and `frameQaUrl`. |
| `GET /v1/files/{path}` | Generated PNG/JSON/media, confined to `ANIMIND_DATA_DIR`. |

Without a Gemini key (or with `ANIMIND_FORCE_MOCK=1`) characters are a flat PIL
drawing cut into the same layers; the response says `"generator": "mock"`.

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

- **debate**: gancho -> tese (quote) -> réplica (quote) -> réplica -> réplica (quote, <= 20 words) -> tréplica (quote) -> two fechamentos.
- **lesson**: gancho -> ponto 1 (quote) -> ponto 2 -> ponto 3 (quote) -> exemplo -> resumo. **solo**: gancho -> tese (quote) -> argumento -> fechamento.
- Each beat has a word budget (`maxS` = words / 2.6 + 0.8 s) and a pause after it. A clip that overruns
  `maxS` is voiced again at `+15%` (`ttsRate`).
- `kind: "quote"`: the text is copied by code from the sheet, byte for byte, with
  `quote {id, text, source, label, url, site, wikisource?}` (`label` is the short on-screen source: no
  page numbers, translators or publishers, <= 48 chars; `source` stays complete). Gemini (`gemini-2.5-flash`, one text call) only picks
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
- Each quote beat ranks the speaker's quotes by topic fit (shared stems plus related concepts) and by
  overlap with the line before it, so a quote answers the previous argument. Every line goes through
  `tidy()` (no space before punctuation, no "?." or "..", capital first letter, final mark).
- Without Gemini (no key, no credit, or `use_llm=False`) the template writer still uses the sheet quotes and
  writes short replies that pick up a noun from the previous line and name the other speaker
  (hook -> exchange -> climax -> close); no line repeats. Gemini answers are cached on disk by
  model + prompt (`ANIMIND_DATA_DIR/cache/script/`), so re-rendering the same script costs nothing.
- Result adds `writer` (`gemini`|`template`), `quoteLines`, `topic` and `personas` (sheet link, quote count, CC BY-SA 4.0).
  The character name is cut before a comma or parenthesis to find the person ("Sócrates, de toga" -> "Sócrates").

## Env

- `CORE_API_KEY` (required; `Authorization: Bearer ...`).
- `GEMINI_API_KEY` (absent -> mock art). `ANIMIND_DATA_DIR` (default `~/.animind-core`, outside git). `CORE_PUBLIC_URL`.
- `ANIMIND_PERSONAS_OFFLINE=1`: never fetch Wikiquote (tests); only cached sheets are used.
- `RHUBARB_PATH`: Rhubarb Lip Sync binary (Linux has no auto-download; absent -> envelope visemes).
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
6. **Render QA** (`frameqa.py`): the encoded MP4 is decoded frame by frame; the render fails closed
   unless every frame has exactly one mouth and stays within the natural opening cap.

**Cost:** $0.039/image. No retries = 3 images = ~$0.12. Test-phase cap 0.30 allows up to ~7 images;
production should use a lower cap (see `.env.example`).

**Image models:** `image_models.py` holds the model registry (nano-banana default; Flux listed as a
pluggable, not-yet-wired provider), config via `ANIMIND_IMAGE_MODEL` / `ANIMIND_IMAGE_MODELS`, and a
per-user/per-model monthly credit ledger (`ANIMIND_FREE_CREDITS`). Nothing is charged; keys are read
from env by name only.
