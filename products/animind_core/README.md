# Animind core API (v1)

Small HTTP API that the Animind app calls at `CORE_URL/v1/*` (see `lib/contract.ts`
in github.com/diogolean/animind). It wraps existing code only:

- `POST /v1/characters` -> `products/aiwake/animator/factory/one_shot_factory.py`
  `OneShotPuppetFactory.create_character` (Gemini image via
  `create_puppet.generate_character_image`, BiRefNet cutout, jaw slice,
  `animation.parametric.factory.rigger.auto_rig_character` for eyes and Rhubarb
  mouths A-H/X), then `OneShotPuppetFactory.generate_view` for `facing_right` and
  `facing_front`. Returns the contract `Character` plus `views`, `visemeMap`, `manifestUrl`.
- `POST /v1/render`, `GET /v1/jobs/{id}` -> edge-tts voices + `animation.parametric.scene_render.render_scene`.
- `GET /v1/files/{path}` serves generated PNG/JSON/media from `ANIMIND_DATA_DIR`.

Env: `CORE_API_KEY` (required, `Authorization: Bearer`), `GEMINI_API_KEY` (absent -> mock art),
`ANIMIND_DATA_DIR` (default `~/.animind-core`, outside git), `CORE_PUBLIC_URL`,
`MAX_GOOGLE_COST_PER_RUN_USD` (the shared guardrail; reset per character request, clamped to $0.05
unless `GOOGLE_COST_ALLOW_ABOVE_CEILING=1`). One view costs ~$0.04; all 3 views ~$0.12, so the
right and front views need that opt-in, otherwise they come back with a `failed: GOOGLE BUDGET` status.

```bash
pip install -r requirements.txt -r products/animind_core/requirements.txt
CORE_API_KEY=... uvicorn products.animind_core.app:app --port 8787
```
