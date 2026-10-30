# Animind core API (contract v2)

Small HTTP API that the Animind app calls at `ANIMIND_CORE_URL/v1/*` (see
`lib/contract.ts` in github.com/diogolean/animind). URL prefix `v1`, payloads
follow contract **v2** (views `front`/`left`/`right`, each with layers + 9
Rhubarb visemes).

| Endpoint | What runs |
| --- | --- |
| `POST /v1/characters` | `rig.py`: Gemini image via Aiwake `create_puppet.generate_character_image` (closed mouth, then "open" and "round" edits with the closed shot as reference) -> BiRefNet matte (rembg `birefnet-general`, as in the Rigly rigger) -> magenta despill -> Gemini feature boxes (face, eyes, mouth) -> layers `body`, `head`, `eyes`, `eyes_closed` + mouth sprites `X A B C D E F G H`. `right` is the mirror of `left`; `front` reuses the left art for now. |
| `POST /v1/render`, `GET /v1/jobs/{id}` | edge-tts per line -> Rhubarb visemes per line (`animation.parametric.rhubarb`, envelope fallback) with absolute times and a `view` per line -> `scene.py` compositor with the cinematic look of `look.py` (built sets per scenario on parallax plates (perspective floor under a 0.6 H horizon, set pieces and practical lights that match the rim colours, depth-of-field blur), each bust standing behind a lectern that hides the cut with a contact shadow on the floor, rim/key light, set ambient and occlusion baked into the rig layers, shot grammar (wide establishing shot, over-the-shoulder singles with the listener out of focus, a close-up cutaway on every real quote, a closing two-shot; the lesson cuts to the board for quotes) with hard cuts, dissolves and whip pans plus push-in and handheld drift, grade LUT + vignette, captions and lower-thirds with name and source; all precomputed per job and deterministic, ~0.1 s/frame on CPU) -> Rigly `animation.parametric.renderer.AnimationRenderer` (ffmpeg) -> `jobs/<id>/preview.mp4` (1280x720, 24 fps, parody label baked in). |
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
  `quote {id, text, source, url, site, wikisource?}`. Gemini (`gemini-2.5-flash`, one text call) only picks
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
- Result adds `writer` (`gemini`|`template`), `topic` and `personas` (sheet link, quote count, CC BY-SA 4.0).
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
