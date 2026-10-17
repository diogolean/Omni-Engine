# Unified Multi-Page Content Factory

> **A multi-persona, AI-powered social-media production engine** that generates brand-aligned images, kinetic video reels, and scheduled postplanner sheets across multiple independent page identities — from a single CLI command.

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
  - [Page / Persona Orchestration](#page--persona-orchestration)
  - [Central Data Structures](#central-data-structures)
  - [Post Types and Formats](#post-types-and-formats)
  - [Video Pipeline](#video-pipeline)
- [Integrated APIs](#integrated-apis)
- [Cost-Optimization and Rate-Limit Strategy](#cost-optimization-and-rate-limit-strategy)
- [Concurrency Model](#concurrency-model)
- [Directory Structure](#directory-structure)
- [Setup](#setup)
- [Usage](#usage)
- [Page Configuration](#page-configuration)
- [Pinterest Engine](#pinterest-engine)
- [License](#license)

---

## Overview

The Unified Multi-Page Factory is a production-grade Python automation pipeline built for high-volume social media content creation. It orchestrates:

- **Multi-page persona switching** — each page (e.g. `wonder_feed`, `anna_protocol`, `master_mei`) has isolated DNA, assets, and output directories.
- **Dual AI brain** — economic (DeepSeek + Gemini Flash) or premium (Claude + Gemini Pro) text generation chains selected per run.
- **Gemini image generation** — atmospheric graphite sketches, full-bleed hyper-literal backgrounds, or avatar-composited portraits.
- **ECONOMIC_REEL pipeline** — vertical 1080x1920 MP4 with ElevenLabs TTS voiceover, word-level subtitle burn, ambient soundscape, dual-stage Ken Burns zoom, and cinematic film grain.
- **Automatic postplanner export** — XLSX sheets compatible with bulk scheduling tools, populated with B2/ImgBB CDN URLs.
- **Pinterest publishing engine** — OAuth-authenticated pin creation with human-mimicking drip scheduling.

---

## Architecture

How the factory is deployed, and the decisions behind B2, the runners, and reel QA, is in [docs/architecture/overview.md](docs/architecture/overview.md).

### Page / Persona Orchestration

Page selection is a **bootstrap-time binding**, not a runtime swap.

```
main.py --page wonder_feed --post-type ECONOMIC_REEL --qty 3
         │
         ├─► _preparse_active_page() sets ACTIVE_PAGE env var
         ├─► config.py resolves all page-scoped paths
         └─► channel_loader.load_page_context() builds PageContext
                  │
                  └─► imports channels_config/{page_id}/page_config.py
                       └─► master_dna.json  (persona voice, environments, CTAs)
```

Each persona lives under `channels_config/{page_id}/`:

| Asset | Purpose |
|---|---|
| `master_dna.json` | Persona voice, metaphors, environments, CTAs |
| `persona_dna.py` | Python accessors over the DNA JSON |
| `page_config.py` | Runtime overrides: typography, ElevenLabs voice, `TOPIC_POOL`, sketch/horror flags, reel dimensions |
| `avatar_reference/` | Likeness PNG (when `--avatar ON`) |
| `logo/` | Brand watermark PNG |
| `style_reference/` | Aesthetic reference screenshots for image-to-image guidance |

Valid built-in pages: `anna_protocol`, `master_mei`, `wonder_feed`, `down_dirty`, `ancient_knowledge`, `momma_circle`, `principles_of_wealth_finance_economics`.

All outputs are namespaced under `outputs/{page_id}/` — pages never share output directories.

---

### Central Data Structures

No database. Durable state is flat-file JSON + XLSX.

| Store | Path | Purpose |
|---|---|---|
| `content_library.json` | `outputs/{page}/content_library.json` | Append-only index of published-ready rows for the scheduler |
| Per-post durable JSON | `outputs/{page}/library/post_*.json` | Full per-variant checkpoint (topic, caption, URLs, B2 link) |
| `automated_bulk_posts_import.xlsx` | `outputs/{page}/automated_bulk_posts_import.xlsx` | PostPlanner 3-column bulk import (DATE/TIME, CAPTION, MEDIA URL) |
| `session_hooks_cache.json` | `outputs/{page}/session_hooks_cache.json` | Short-term LLM memory — used hooks appended each run to prevent repetition |
| `master_inventory.json` | `outputs/{page}/master_inventory.json` | Multi-platform canonical inventory built by Pinterest sync |

**Durable JSON shape (ECONOMIC_REEL)**:
```json
{
  "page_id": "wonder_feed",
  "post_type": "ECONOMIC_REEL",
  "post_format": "DYNAMIC_REEL",
  "topic": "Why we stay when we should leave",
  "overlay_text": "You stayed because you thought love was enough.",
  "humanized_caption": "...",
  "b2_url": "https://s3.us-east-005.backblazeb2.com/MediaupscaleStorage/...",
  "imgbb_url": "",
  "caption_status": "FINAL",
  "created_utc": "2026-06-05T14:22:11Z"
}
```

Write helpers: `durable_library.py` — `write_atomic_json` / `merge_update_json`.

---

### Post Types and Formats

| Post Type (`--post-type`) | Description |
|---|---|
| `STANDARD_QUOTE` | Research → humanize caption → image with text overlay and logo |
| `SMART_BAIT` | Ultra-short engagement bait overlay; 4-layer image stack; no avatar |
| `LONG_CAPTION_IMAGE` | Long FB-style caption; clean illustration + logo (no baked text) |
| `ECONOMIC_REEL` | Graphite sketch base → vertical MP4 reel with TTS + ambient audio |

| Post Format (`--format`) | Description |
|---|---|
| `IMAGE_AVATAR` | Portrait image with optional avatar composite |
| `IMAGE_BACKGROUND` | Hyper-literal Gemini background + text (SMART_BAIT default) |
| `IMAGE_QUOTE` | Legacy text-on-image |
| `HYBRID_VIDEO` | 7-second Ken Burns loop via `video_converter.py` |
| `TEXT_QUOTE` | Solid brand backdrop + text; no Gemini image call |
| `DYNAMIC_REEL` | Full ECONOMIC_REEL video pipeline |

`wonder_feed` automatically forces `ECONOMIC_REEL` toward `DYNAMIC_REEL` and locks the draw style to `SKETCH`.

---

### Video Pipeline

**File:** `agents/media/video_engine.py` → `compile_dynamic_reel()`

```
Graphite PNG (logo-free)
        │
        ▼
[1080x1920 fit, 30 fps]
        │
        ├─► Dual-stage Ken Burns zoom
        │     Phase 1 (0–16s):  scale 1.0 → 1.35  (fast pull-in)
        │     Phase 2 (16s–end): scale 1.35 → 1.15 (ease out)
        │
        ├─► Dark vignette overlay  (overlay_opacity ≈ 0.45)
        ├─► Hook text layer        (static, centered, Poppins-Bold)
        ├─► Word-level subtitles   (ElevenLabs timestamps → binary search)
        ├─► Logo layer             (static PNG, post-zoom, page-tuned opacity)
        ├─► Film grain             (cinematic noise overlay)
        │
        ▼
   ElevenLabs voiceover MP3
        + ambient SFX MP3 (22% volume, max 22s)
        │
        ▼
   H.264 / AAC MP4  →  Backblaze B2 upload  →  PostPlanner XLSX
```

Duration = `max(page.REEL_DURATION, audio_length + 2s tail)`.

---

## Integrated APIs

| API | Role | Handler |
|---|---|---|
| **Google Gemini** | Image generation, research, economic text brain | `providers/image_provider.py`, `providers/gemini_utils.py`, `caption_engine.py` |
| **Anthropic Claude** | Premium caption polish / humanizer | `caption_engine.py` |
| **DeepSeek** | Economic text brain (research, bait, captions) | `caption_engine.py` (OpenAI-compatible client) |
| **ElevenLabs** | TTS voiceover + word timestamps + ambient SFX | `agents/media/audio_engine.py` |
| **ImgBB** | Image hosting → MEDIA URL for static posts | `agents/media/imgbb_client.py` |
| **Backblaze B2** | Video hosting → MEDIA URL for reels | `agents/media/b2_client.py` |
| **Pinterest API v5** | Pin creation, OAuth, board listing, drip schedule | `agents/posting/pinterest_engine/publisher.py`, `scheduler.py` |

> **Note:** Meta (Facebook/IG) and TikTok fields exist in the inventory schema as future-ready placeholders; live publishing to those platforms is not yet implemented.

All credentials are stored exclusively in `.env` (never committed) and loaded through `config.py`.

---

## Cost-Optimization and Rate-Limit Strategy

### Economic mode (`--economic`)

Activating economic mode swaps every heavy LLM call to a cheaper provider:

| Component | Premium | Economic |
|---|---|---|
| Text brain | Gemini 2.5 Flash (+ Claude polish) | Gemini 2.5 Flash primary (DeepSeek last-resort only) |
| Image model | `gemini-3-pro-image-preview` | `gemini-3.1-flash-image` |
| Research phase | Full PDF corpus sweep | Skipped (direct hook generation) |

### ElevenLabs minimization
- TTS is invoked **only** for `ECONOMIC_REEL`, never for static image posts.
- Ambient SFX capped at **22 seconds** per request (`_SFX_MAX_DURATION`).
- Ambient mixed at **22% volume** (`_AMBIENT_VOLUME`); failure degrades gracefully to voice-only.
- Voiceover skipped entirely when the API key is absent or script is empty.

### Rate-limit safeguards
- Gemini image retries with exponential back-off; on repeated failure the premium model is tried.
- Pinterest scheduler (`scheduler.py`) uses human-mimic timing with randomized inter-pin delays.
- `write_lock` serializes all shared file writes to prevent race conditions across parallel workers.

---

## Concurrency Model

```
main.py
  └─► ThreadPoolExecutor(max_workers=min(qty, 5))
          │
          ├─► worker 1 ──┐
          ├─► worker 2   │  (image API, LLM, audio — fully parallel)
          ├─► worker 3   │
          └─► ...        │
                         ▼
                    write_lock  ──► automated_bulk_posts_import.xlsx
                                ──► content_library.json
                    hooks_cache_lock ──► session_hooks_cache.json
```

- `qty == 1` runs synchronously (no thread overhead).
- `qty > 1` fans out up to **5 workers**; failed variants are logged and skipped without crashing the batch.
- All shared-file I/O is protected by `threading.Lock`.

---

## Project structure rules

The repository root holds entry points, config, and top-level packages only: `main.py`, `config.py`, `channel_loader.py`, `requirements.txt`, `.env`, and packages such as `agents/`, `channels_config/`, `core/`, `models_avatar/`, `utils/`, and `tests/`.

- Temp, preview, review, and log files go in the per-project folder under `outputs/<project>/`. Use `temp_path(channel, *parts)` and `review_path(channel, *parts)` from `utils/pipeline_paths.py` (`outputs/<channel>/_tmp/` and `outputs/<channel>/_review/`). Do not create `_tmp*`, `_cp_preview`, `_review`, or log folders at the repo root.
- Tests live in that project's `tests/` folder (`tests/` or `channels_config/<channel>/tests/`).
- Unused temp folders are deleted once nothing references them.

See `docs/PROJECT_STRUCTURE.md`.

---

## Directory Structure

```
Unified Multi-Page Factory/
├── main.py                     # Primary CLI orchestrator
├── config.py                   # Credentials, model IDs, ACTIVE_PAGE paths
├── channel_loader.py           # Channel-config bootstrap (PageContext; CLI `--page`)
├── omni/                       # Publisher interface, brand adapters, drip worker
├── config/brandings.yaml       # N brandings × N platforms (active flag)
├── Dockerfile                  # linux/arm64 runtime (Oracle VM)
├── docker-compose.yml          # engine + scheduler
├── .github/workflows/          # PR validation, GHCR build, SSH deploy
├── utils/run_ledger.py         # Per-run model/banner registry → outputs/logs/
├── requirements.txt
├── .env                        # Factory secrets — NOT committed, stays at root
│
├── agents/
│   ├── writer/                 # Captions + LOFI scripts (script_agent, caption_engine)
│   ├── rag/                    # Channel RAG + inspectable LOFI lookup (lofi_rag)
│   ├── mcp/                    # model_api_flows.py + text_model.py (LOFI_SCRIPT_MODEL)
│   ├── orchestrator/           # Agentic pipeline
│   ├── media/                  # Audio, video, providers, VisualArchitect
│   │   ├── avatar_engine/      # mei_visual + visual_architect
│   │   └── providers/          # Image adapters (Together / Gemini / remote GPU)
│   └── posting/                # YouTube / Pinterest / Facebook CLIs
│       ├── pinterest_main.py
│       ├── pinterest_oauth.py
│       ├── publish_existing.py
│       ├── youtube_publisher.py
│       ├── facebook_scheduler/
│       └── pinterest_engine/
│
├── core/                       # Reel engines (was core_engine/)
│   ├── economic_reel_lofi/
│   ├── principles_of_wealth/
│   └── reel_sequence_engine.py
│
├── quality/VisualQA_Agent/     # Generate → critique → auto-correct
├── utils/run_ledger.py         # Per-run model/banner registry
├── infra/runpod/workflows/     # ComfyUI workflow JSON
├── models_avatar/              # AI-model personas (Sophie Bennet). Not a video channel.
├── tests/                      # Former root test_*.py
├── scripts/                    # One-offs
│
├── channels_config/
│   ├── wonder_feed/
│   ├── anna_protocol/
│   ├── master_mei/
│   ├── down_dirty/
│   ├── momma_circle/
│   ├── ancient_knowledge/      # includes .env.ancient_knowledge + token_ancient_knowledge.json
│   └── principles_of_wealth_finance_economics/
│       └── wealth_main.py      # Library-ingest + YouTube publish CLI
│
├── docs/                       # Architecture + command docs
│   └── architecture/           # DEPRECATED — artifacts live in portfolio_showcase/
│
└── outputs/                    # NOT committed (generated at runtime)
    └── {page_id}/
        ├── content_library.json
        ├── assets/
        └── library/
```

---

## Setup

### Prerequisites

- Python 3.10+
- [Git for Windows](https://git-scm.com/) (or Git on Linux/macOS)
- FFmpeg on `PATH` (required by MoviePy for video export)

### Installation

```bash
git clone https://github.com/MediaUpScale/Unified-Multi-Page-Factory-edition.git
cd Unified-Multi-Page-Factory-edition
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-ci.txt   # pytest + ruff, for the PR checks
```

### Environment Variables

Copy `.env.example` (or create `.env`) and populate:

```ini
# LLM providers
GEMINI_API_KEY=
ANTHROPIC_API_KEY=
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEEPSEEK_MODEL=deepseek-v4-flash
DEEPSEEK_PRO_MODEL=deepseek-v4-pro

# Media
ELEVENLABS_API_KEY=
IMGBB_API_KEY=

# Backblaze B2
B2_KEY_ID=
B2_APPLICATION_KEY=
B2_BUCKET_NAME=
B2_ENDPOINT_URL=https://s3.us-east-005.backblazeb2.com

# Pinterest
PINTEREST_ACCESS_TOKEN=
PINTEREST_BOARD_ID=

# Factory media root (Google Drive on the owner machine)
OUTPUT_PATH=

# AI-model avatars (models_avatar). Pod URL falls back to REMOTE_GPU_BASE_URL.
CIVITAI_API_TOKEN=
MODELS_AVATAR_BACKEND=runpod_pod
MODELS_AVATAR_POD_URL=
```

---

## Usage

### Generate content for a page

```bash
# 1 ECONOMIC_REEL for wonder_feed (economic mode, sketch style)
python main.py --channel wonder_feed --post-type ECONOMIC_REEL --economic --qty 1

# 3 SMART_BAIT images for anna_protocol (premium, avatar OFF)
python main.py --channel anna_protocol --post-type SMART_BAIT --avatar OFF --qty 3

# 5 STANDARD_QUOTE images for master_mei (economic)
python main.py --channel master_mei --post-type STANDARD_QUOTE --economic --qty 5
```

### Retroactive B2 upload + PostPlanner

```bash
# Upload an existing clips directory and generate postplanner
python upload_clips_to_b2.py --clips-dir outputs/wonder_feed/clips/
```

### Pinterest engine

```bash
# First-time OAuth
python agents/posting/pinterest_oauth.py

# Sync, transform, and schedule pins
python agents/posting/pinterest_main.py schedule --channel wonder_feed
```

---

## Page Configuration

Each page is self-contained. The minimal `page_config.py` shape:

```python
PAGE_ID          = "wonder_feed"
PAGE_DISPLAY_NAME = "Wonder Feed"
NICHE            = "Relationship Psychology"

# ElevenLabs voice for ECONOMIC_REEL
ELEVENLABS_VOICE_ID = "Dorothy"
ELEVENLABS_MODEL    = "eleven_multilingual_v2"

# Reel layout
SUBTITLE_FONTSIZE    = 70
SUBTITLE_Y_POSITION  = 1400
LOGO_WIDTH           = 380
LOGO_MAX_HEIGHT      = 95
LOGO_OPACITY         = 0.98
LOGO_BOTTOM_MARGIN   = 100
HOOK_Y_FRAC          = 0.50
REEL_OVERLAY_OPACITY = 0.45
REEL_DURATION        = 45

# Dynamic topic pool (prevents script repetition)
TOPIC_POOL = [
    "Why we stay past the expiration date",
    "The psychology of trauma bonding",
    # ... 20+ topics
]

# Image style lock
ILLUSTRATION_STYLE  = "charcoal sketch, high contrast, dark azure shadows"
STYLE_CHARACTERS    = "melancholic portrait subject, serpentine horror mask"
```

Add a new page by:
1. Creating `channels_config/{your_page_id}/` with the files above.
2. Adding `master_dna.json`, `logo/logo.png`, and (optionally) `avatar_reference/`.
3. Running `python main.py --page your_page_id --post-type STANDARD_QUOTE`.

---

## Pinterest Engine

The Pinterest sub-engine operates independently via `agents/posting/pinterest_main.py`:

| Action | Description |
|---|---|
| `sync` | Pulls existing pins into `master_inventory.json` |
| `transform` | Converts library assets to 2:3 sales-pin format |
| `schedule` | Drip-publishes transformed pins with human-mimic delays |
| `status` | Reports inventory readiness and scheduling queue |

OAuth tokens are obtained once via `agents/posting/pinterest_oauth.py`. Channel tokens for Ancient Knowledge land in `channels_config/ancient_knowledge/.env.ancient_knowledge`; other channels use `channels_config/<id>/.env` or the factory-root `.env`.

`config/brandings.yaml` lists every branding and platform. The VM scheduler reads `config/pinterest_accounts.yaml` and publishes image cards only, one B2 object at a time. Ancient Knowledge links each pin to its article on `https://ancientknowledge.eokviral.com`. Anna's Garden (`anna_protocol`) keeps `TODO` for its site and board until those are known. Tokens stay in the env vars named in that file. Brandings still publish only where `active: true`. Ancient Knowledge and Anna's Protocol each have their own token and board (`PINTEREST_ACCESS_TOKEN_ANCIENT_KNOWLEDGE`, `PINTEREST_ACCESS_TOKEN_ANNA_PROTOCOL`, and the matching `PINTEREST_BOARD_ID_*` vars). The CLI is unchanged:

```bash
python agents/posting/pinterest_main.py schedule --channel ancient_knowledge --quantity 1
python agents/posting/pinterest_main.py status --channel anna_protocol
```

YouTube uploads from `main.py --publish-youtube` still call `upload_short_from_envelope` through `omni.adapters.youtube.publish_envelope`. Facebook stays on the Playwright / Post Planner path.

---

## Deploy

Target: Oracle Cloud VM, Ubuntu 24.04, aarch64, checkout path `/opt/omni-engine`. Docker and the Compose plugin are already installed. The published image is `linux/arm64` at `ghcr.io/diogolean/omni-engine`, tagged with the git SHA and `latest`.

### Local setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-ci.txt
cp .env.example .env
# fill tokens, then:
pytest -q tests/test_omni_config.py tests/test_omni_publisher.py

# Optional: run the same process the VM runs.
# `up -d` starts the engine only. The scheduler is profile "scheduler".
docker compose up -d --build engine
docker compose exec engine python main.py --channel wonder_feed --post-type ECONOMIC_REEL --qty 1
docker compose --profile scheduler logs -f scheduler
```

One scheduler wake posts `PINTEREST_PINS_PER_RUN` image pins (default 1) for each ready account in `config/pinterest_accounts.yaml`. It waits a random gap between `MIN_INTERVAL_HOURS` and `MAX_INTERVAL_HOURS` (default 3–6) before the first publish, then sleeps the same kind of gap between wakes. `--once` and `--dry-run` skip that initial wait. Video objects are skipped. Titles come from the article or DeepSeek, not from the filename. `python -m omni.worker --role scheduler --once` runs a single wake. Add `--dry-run` to build the cards without calling Pinterest.

### GitHub Secrets

Repository secrets (Settings → Secrets and variables → Actions):

| Secret | Value |
|---|---|
| `VM_HOST` | VM public IP or DNS |
| `VM_USER` | SSH user that is in the `docker` group |
| `VM_SSH_KEY` | Private key for that user (full PEM, newlines included) |

`GITHUB_TOKEN` is provided by Actions and is what pushes the image. No extra registry secret.

### What a push to `main` does

1. Builds `linux/arm64` with buildx and pushes `ghcr.io/diogolean/omni-engine:<git-sha>` and `:latest`.
2. Copies `docker-compose.yml` to `/opt/omni-engine/docker-compose.yml`.
3. Sets `OMNI_IMAGE_TAG` in the VM `.env` to that SHA, appends the SHA to `/opt/omni-engine/releases.log`, then runs:

```bash
cd /opt/omni-engine && docker compose pull engine && docker compose up -d engine
```

That starts the **engine only**. The scheduler is not started by deploy. Set `SCHEDULER_ENABLED=1` in the VM `.env` before a deploy when the scheduler should come up with the engine, or start it by hand (see below). Pull requests do not deploy. They run Ruff and the omni pytest modules (`validate.yml`).

### One-time VM setup

```bash
sudo mkdir -p /opt/omni-engine/outputs /opt/omni-engine/credentials/tokens
sudo chown -R "$USER":"$USER" /opt/omni-engine
# secrets file — never commit it
nano /opt/omni-engine/.env
# YouTube OAuth tokens the factory already uses
# credentials/tokens/youtube_token_<page>.json
```

The published-pin ledger is `outputs/pinterest/published_pins.json` on the mounted outputs volume (account, B2 object, and article). Channel env files the operator maintains live at `/opt/omni-engine/channels_config/<channel>/.env` and `.env.<channel>`. Compose mounts that directory read-write at `channels_env/` inside the container so a token refresh survives the next container, without hiding the image's channel package. Root `/opt/omni-engine/.env` is mounted read-write for the same reason.

Ancient Knowledge reads cards from B2 prefix `fbcards/` whose filenames match `^(ancient_|ancientknowl_)` (for example `fbcards/2026-10-04/ancientknowl_10-04_2130_baalbek.jpg`). A filename matches a WordPress post only when the distinctive words agree. Generic words such as `tomb`, `ancient`, and `lines` are ignored, so `giants-tomb-sardinia` does not attach to the `giants` article. A slug can also be the first word of the filename topic plus one extra word, so `baalbek-trilithon` matches `baalbek`. Optional overrides live in `config/pinterest_image_map.yaml` (`baalbek-trilithon: baalbek`, or `skip`). Images with no match are logged and skipped. The card cover-crops the B2 photo to 2:3 and lays the hook on a gradient. Ancient Knowledge sets `use_featured_image: false`, so a missing B2 photo is skipped. That site's featured images are title graphics and its posts have no body photos. Other accounts may still use a body photo, then a featured photo that is not a title card. The article host currently serves a shared `*.web-hosting.com` certificate, so `article_tls_verify: false` in `config/pinterest_accounts.yaml` applies to those article lookups only. Pinterest API calls stay TLS-verified.

Start the scheduler only when you mean to publish:

```bash
cd /opt/omni-engine
# optional: persist it across deploys
# grep -q '^SCHEDULER_ENABLED=' .env || echo 'SCHEDULER_ENABLED=1' >> .env
docker compose --profile scheduler up -d scheduler
```

Dry run (no Pinterest call, no ledger write, no multi-hour wait). Generated cards are written to `outputs/pinterest/dryrun/` so you can open them before anything publishes:

```bash
cd /opt/omni-engine
docker compose --profile scheduler run --rm --no-deps scheduler \
  python -m omni.worker --role scheduler --once --dry-run
ls outputs/pinterest/dryrun
```

### GHCR login on the VM

The first push creates a **private** package. The VM cannot `docker compose pull` until it has logged in, or until you make the package public (GitHub → Packages → omni-engine → Package settings → Change visibility).

Create a GitHub PAT with `read:packages` and, on the VM as `VM_USER`:

```bash
echo '<PAT>' | docker login ghcr.io -u diogolean --password-stdin
```

Docker stores the credential in that user's `~/.docker/config.json`. Later deploys reuse it. `VM_USER` must be able to run `docker` without sudo.

### Rollback

Every deploy records the image tag (the full git SHA) in `/opt/omni-engine/.env` (`OMNI_IMAGE_TAG`) and in `/opt/omni-engine/releases.log`. Images stay in GHCR under that SHA.

From GitHub: Actions → Deploy → Run workflow → set **image tag** to the SHA you want (from `releases.log` or the GHCR package). The workflow skips the build and runs the same pull and `up -d` against that tag.

On the VM, the same steps by hand:

```bash
cd /opt/omni-engine
# edit OMNI_IMAGE_TAG=<previous git sha> in .env
docker compose pull engine
docker compose up -d engine
# scheduler only if SCHEDULER_ENABLED=1, or:
# docker compose --profile scheduler up -d scheduler
```

---

## License

Proprietary — MediaUpScale LLC. All rights reserved.

Internal use only. Do not distribute without authorization.
