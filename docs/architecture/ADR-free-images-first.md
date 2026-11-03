# ADR: Free images first

Status: Accepted
Date: 2026-10-17

## Context

Pinterest cards, Facebook cards, and reel stills were generated with Flux even when a real photograph would serve the topic. That spends image tokens and makes documentary channels look synthetic. The publishable artifact is already a template: a 2:3 card with a hook, a Facebook overlay, or a reel with captions. The background does not have to be a model output.

Commercial use rules out NC and ND licenses. Channels that depend on a likeness, a private library, or a drawn style should keep their current generators.

## Decision

`agents/media/free_images/` searches free-license libraries before the generator:

- Wikimedia Commons, Openverse, NASA Images, Library of Congress
- Smithsonian Open Access, Pexels, and Unsplash when the matching env key is set
- No Google image scraping

A result is kept only when the license is on the allowlist (Public Domain, CC0, CC BY, CC BY-SA, Pexels, Unsplash by default), the short side is at least 1000 pixels after an optional portrait crop, and a corner/stock-watermark heuristic does not fire. An optional vision callable and an optional CLIP score (`FREE_IMAGE_CLIP=1` plus `open_clip`) can tighten that check. They are off unless configured.

Search metadata is cached per query under `outputs/<channel>/_tmp/free_images/cache/`. Full images are downloaded one at a time, under a lock, so a parallel reel does not pull a whole result page. That matches the B2 rule of downloading the one object about to be used.

The chosen file is a template input. `raw_publish_allowed` is false. Attribution (title, author, license, source URL) is stored in a sidecar JSON next to the file, and `credit_line()` produces a caption/article credit. The Pinterest scheduler searches after the owned library photo and before FLUX.1-schnell. A hit is stored on the record as `image_credit` and composited by PinTransformer (`blurred_padding` by default). The pin description stays the theme copy and still ends with the article URL. Card and reel producers still run their existing overlay, hook, and subtitle treatment. The raw download is not the published card.

Channel policy is `image_source_policy` / `IMAGE_SOURCE_POLICY`:

| Policy | Behavior |
|---|---|
| `free_first` | Licensed photo when one passes; otherwise Flux |
| `free_only` | Licensed photo or no image. The generator is not called |
| `ai_only` | Skip the search. Existing generator or owned assets |

Defaults come from `IMAGE_SOURCE_POLICY` on each channel folder (`production/providers/image/free/policy.py` rebuilds the map from that flag). A folder with no flag, and any slug that is not a channel folder, stays `ai_only`. Legacy slugs that are not folders stay in that module: `amazing_feed` and `annas_garden` are `free_first`, `aiwake` is `ai_only`. Anna's Garden aliases resolve to the channel that sets `NATURAL_DRAW_LOCK`.

`aiwake`, Endless Summer Paradise, and Master Mei keep their own pipelines. The shared still hook returns the original generator immediately for `ai_only`, and their packages are not rewritten. The lofi / riso illustration pipeline is also left on Flux: those stills have to pass a drawn-style gate, so a photograph would not be a valid substitute.

Owned B2 photographs still win for a Pinterest card. Free search runs only when that file is missing or is a title graphic. WordPress featured images stay off for Ancient Knowledge.

## Consequences

- Documentary and garden cards can ship a real photograph with a credit line and no image-model call.
- A miss on `free_first` still spends a Flux call. `free_only` fails closed instead.
- Provider outages degrade to the generator (or to a skip, for `free_only`). They do not fail the whole wake.
- Cache files live under the channel temp tree and can be deleted with other scratch.
- NC/ND and unknown licenses never enter the template, even if a provider returns them.

## Configuration

```text
IMAGE_SOURCE_POLICY = "free_first"   # page_config.py
FREE_IMAGE_ALLOW_LICENSES=public_domain,cc0,cc_by,cc_by_sa,pexels,unsplash
FREE_IMAGE_MIN_SHORT_SIDE=1000
PEXELS_API_KEY=
UNSPLASH_ACCESS_KEY=
SMITHSONIAN_API_KEY=
FREE_IMAGE_CLIP=0
```
