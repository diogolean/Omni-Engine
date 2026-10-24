# Diagrams

Linked from the [root README](../../README.md). Boxes name the packages that exist in the tree.

## Architecture

`ContentItem` v1 (`shared.contracts`) is the record production writes and distribution reads. `accounts/` resolves tokens for the publishers. `channels_config/` is data: one folder per channel, read by `channel_profile_rows()`. The scheduler walks `config/pinterest_accounts.yaml`. Neither list is a fixed count.

```mermaid
flowchart LR
  subgraph PROD[production]
    PL[pipeline stages] --> PV[providers: llm, image, tts]
    PL --> MD[media: stills, compose, render]
    PL --> QA[qa: judges and mp4 gate]
    PL --> CO[cost: spend ledger]
    FM[formats] --> PL
  end
  subgraph ANIM[animation]
    API[animation.api] --> RIG[parametric renderer]
  end
  subgraph DIST[distribution]
    WK[workers] --> SC[scheduling: drip, ledger, queue]
    SC --> PB[publishers]
    PB --> APIP[api: YouTube, Pinterest]
    PB --> BRW[browser: facebook_scheduler]
    PB --> EXP[export: Post Planner xlsx]
  end
  ACC[accounts] --> DIST
  CH[channels_config] -. data .-> PROD
  CH -. data .-> DIST
  AW[products/aiwake] --> API
  AW --> PB
  PROD -- ContentItem --> LIB[(library: outputs and B2)]
  LIB --> SC
  PROD --> SH[shared]
  DIST --> SH
  ANIM --> SH
  AW --> SH
```

Import rules are in the [README](../../README.md#layers). `animation` reaches `shared` only. A show calls `animation.api` and the publisher protocol.

## Production reel pipeline

`python main.py` enters `production/pipeline/cli.py`. The variant worker in `production/pipeline/stages/variant.py` runs the stages in this order.

`ECONOMIC_REEL_LOFI` leaves before this worker and runs `production/formats/lofi/pipeline.py`. `REFERENCE_BASED_REELS` runs `production/formats/reference/reference_reel_engine.py`. Both still seal the MP4 before a row is publishable.

```mermaid
flowchart TD
  cli["python main.py"] --> setup[stage_setup]
  setup --> still[stage_render_still]
  still --> plan[stage_sequence_plan]
  plan --> seq[stage_sequence_render]
  seq --> brand[stage_caption_brand]
  brand --> compile[stage_compile_reel]
  compile --> gate[production.qa seal]
  gate -->|pass| pub[stage_publish]
  gate -->|fail closed| block[block the row]
  pub --> item[ContentItem in the channel library]
  item --> planner[distribution/publishers/export/post_planner.py]
  item --> yt[distribution/publishers/api/youtube.py]
  planner --> wp[WordPress bot reads the xlsx]
  wp --> fb[Facebook]
```

Inside a reel, media moves like this. Ken Burns is the default motion. The depth-parallax hook stays off unless `REEL_HOOK_PARALLAX=1`. The renderer is ffmpeg unless `REEL_RENDERER=moviepy`.

```mermaid
flowchart TD
  script[Voiceover script] --> tts[TTS and word timestamps]
  tts --> beats[Beat splitter, about 4 to 6 seconds]
  beats --> stills[One image per beat]
  stills --> vgate[Spoken-line vision gate]
  vgate -->|pass| mux[Compile MP4]
  vgate -->|fail or judge down| block[Block publish and log]
  mux --> mp4[MP4 gate: frames, audio, duration]
  mp4 -->|pass| report[QA report JSON]
  mp4 -->|reject| block
  report --> planner[Post Planner xlsx]
  report --> youtube[YouTube private plus publishAt]
  report --> b2[Backblaze B2 object]
  b2 --> pinterest[VM Pinterest drip]
```

Images prefer a free photo (Wikimedia Commons, then Openverse), then a reusable `scene_library` still, then Flux-schnell for the scene types that channel lists as safe. Flux-schnell does not draw a named artefact. Nano Banana stays off (`REEL_NANOBANANA_MAX_PER_REEL=0`).

## Pin pipeline

Pin copy, the article match, and the 2:3 card are produced in `production/formats/pins/`. Publishing is distribution.

```mermaid
flowchart LR
  pins[production/formats/pins] --> lib[channel library and B2]
  lib --> drip[distribution/scheduling/drip.py]
  sch["workers --role scheduler"] --> drip
  drip --> ledger["ledger OUTPUT_PATH/_omni/publish_ledger.json"]
  drip --> api[distribution/publishers/api/pinterest.py]
  ops["tools/pinterest_cli.py schedule"] --> api
  lib --> csv["tools/pinterest_cli.py export"]
  csv --> planner[Post Planner CSV]
```

`python tools/channel_cli.py --channel <id>` forwards to that Pinterest CLI when the channel folder has no `cli.py`.

## Distribution workers

```mermaid
flowchart TD
  entry["python -m distribution.workers"] --> role{--role}
  role -->|engine| idle["idle parent for docker compose exec engine python main.py"]
  role -->|scheduler| wake[drip wake on a 7 to 9 hour gap]
  wake --> ledger[publish ledger]
  wake --> pubs[publishers]
  pubs --> yt[api.youtube]
  pubs --> pin[api.pinterest]
  pubs --> fb[browser.facebook_scheduler]
  pubs --> exp[export.post_planner]
  acct["--account NAME"] --> one[one account, then exit]
  one --> ledger
```

`--once` runs a single wake and exits. `--dry-run` builds cards and logs them, and skips the Pinterest call and the ledger write. `--pins` sets the count for `--account`. A dry-run with `--account` and no `--pins` renders 5.

The Compose service names stay `engine` and `scheduler`. Deploy starts `engine` only. The scheduler is profile `scheduler` and stays stopped unless `SCHEDULER_ENABLED=1` or an operator starts the profile. One scheduler replica. The ledger is a file.

Logger names `omni.worker` and `omni.pinterest_drip` stay. The package `omni/` is gone.

## Deploy

Validate (`.github/workflows/validate.yml`) runs on every pull request and on every push to `main`. Deploy (`.github/workflows/deploy.yml`) starts from `workflow_run` after that Validate run completes. The build job runs when the Validate run succeeded, the event was a push, and the branch was `main`.

```mermaid
flowchart TD
  pr[pull request] --> val[Validate: lint, factory tests, Aiwake tests, gitleaks]
  push[push to main] --> val
  val -->|success and the event was a push to main| wr[workflow_run]
  wr --> build[Deploy: build linux/arm64 from deploy/docker/Dockerfile]
  build --> ghcr["GHCR ghcr.io/diogolean/omni-engine:sha and :latest"]
  ghcr --> vm["Oracle VM: pull engine and up -d engine"]
  dispatch["workflow_dispatch image_tag"] --> roll[pull an image already in GHCR]
  roll --> vm
```

`image_tag` is required. It is a 40-character SHA or `latest`. Dispatch does not rebuild and does not deploy the SHA of whichever ref was selected in the Actions UI.

The remote step copies `deploy/compose/docker-compose.yml` to `/opt/omni-engine/docker-compose.yml` and runs:

```bash
cd /opt/omni-engine && docker compose pull engine && docker compose up -d engine
```

Local compose passes the repo root as the project directory. Commands are in [../COMMAND_REFERENCE.md](../COMMAND_REFERENCE.md) and [../deploy.md](../deploy.md).

## Pinterest publish sequence

```mermaid
sequenceDiagram
  participant sch as VM scheduler
  participant b2 as Backblaze B2
  participant led as publish ledger
  participant wp as WordPress
  participant pin as Pinterest API

  sch->>b2: list image keys under the account prefix
  b2-->>sch: keys only, no bodies
  sch->>led: drop keys already recorded for this account and board
  sch->>wp: fetch the article catalog
  wp-->>sch: slug, title, body
  sch->>sch: match one filename to one article
  sch->>b2: download that single object
  sch->>sch: cover-crop a 2:3 card
  sch->>pin: create the pin
  pin-->>sch: pin id
  sch->>led: record account, B2 object, article, pin id
  sch->>sch: delete the temp file
```
