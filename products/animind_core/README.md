# Animind core API (contract v2)

Small HTTP API that the Animind app calls at `ANIMIND_CORE_URL/v1/*` (see
`lib/contract.ts` in github.com/diogolean/animind). URL prefix `v1`, payloads
follow contract **v2** (views `front`/`left`/`right`, each with layers + 9
Rhubarb visemes).

| Endpoint | What runs |
| --- | --- |
| `POST /v1/characters` | `rig.py`: Gemini image via Aiwake `create_puppet.generate_character_image` (closed mouth, then "open" and "round" edits with the closed shot as reference) -> BiRefNet matte (rembg `birefnet-general`, as in the Rigly rigger) -> magenta despill -> Gemini feature boxes (face, eyes, mouth) -> layers `body`, `head`, `eyes`, `eyes_closed` + mouth sprites `X A B C D E F G H`. `right` is the mirror of `left`; `front` reuses the left art for now. |
| `POST /v1/render`, `GET /v1/jobs/{id}` | edge-tts per line -> Rhubarb visemes per line (`animation.parametric.rhubarb`, envelope fallback) with absolute times and a `view` per line -> `scene.py` compositor with the cinematic look of `look.py` (parallax scenario plates with depth-of-field blur, rim/key light and soft drop shadows baked into the rig layers, camera push-in + handheld drift + reframing per speaker with cross-dissolves and whip pans, grade LUT + vignette, captions and lower-thirds with name and source; all precomputed per job and deterministic, ~0.1 s/frame on CPU) -> Rigly `animation.parametric.renderer.AnimationRenderer` (ffmpeg) -> `jobs/<id>/preview.mp4` (1280x720, 24 fps, parody label baked in). |
| `GET /v1/files/{path}` | Generated PNG/JSON/media, confined to `ANIMIND_DATA_DIR`. |

Without a Gemini key (or with `ANIMIND_FORCE_MOCK=1`) characters are a flat PIL
drawing cut into the same layers; the response says `"generator": "mock"`.

## Scripts: real quotes, marked paraphrases

`script.py` writes every render from a beat plan, and `personas.py` keeps a sheet of real quotes per
character (Wikiquote MediaWiki API, cached as YAML in `ANIMIND_DATA_DIR/personas/<lang>/`, outside git;
"not found" is cached for 7 days).

- **debate**: gancho -> tese (quote) -> réplica (quote) -> two short alternating réplicas -> tréplica (quote) -> two fechamentos.
- **lesson**: gancho -> ponto 1 (quote) -> ponto 2 -> ponto 3 (quote) -> exemplo -> resumo. **solo**: gancho -> tese (quote) -> argumento -> fechamento.
- Each beat has a word budget (`maxS` = words / 2.6 + 0.8 s) and a pause after it. A clip that overruns
  `maxS` is voiced again at `+15%` (`ttsRate`).
- `kind: "quote"`: the text is copied by code from the sheet, byte for byte, with
  `quote {id, text, source, url, site, wikisource?}`. Gemini (`gemini-2.5-flash`, one text call) only picks
  quote ids and writes the other lines. An unknown or reused id becomes the best topic match from the
  sheet, never model text.
- `kind: "paraphrase"`: written lines, never shown as quotes. Quote marks are removed, and a line that
  repeats 7+ words of a quote is replaced. `basis` names the quote it restates, if any. With no sheet
  (fictional characters, offline) every line is a paraphrase.
- On screen: a chip "CITAÇÃO REAL · source · Wikiquote" or "PARÁFRASE"; quote captions in italics
  within “ ”; captions paged two rows at a time. The lesson board shows the beat, the quote and its source.
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
