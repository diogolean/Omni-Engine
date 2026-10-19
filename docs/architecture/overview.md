# Architecture

Where Omni-Engine runs, how a piece of content moves, and which decisions are already settled. Diagrams are in [diagrams.md](diagrams.md). Decision records are in [decisions/](decisions/). `main.py` is the CLI entry. The steps it used to hold live in `engine/`. The map is [main-module-map.md](main-module-map.md). The root allowlist is [REPO_LAYOUT.md](REPO_LAYOUT.md).

Older showcase notes that used to live in this folder now live under `portfolio_showcase/`. See [DEPRECATED.md](DEPRECATED.md).

## System map

```
PC runner (Windows / GPU)          Oracle VM (Docker)              Backblaze B2
Aiwake animated renders            engine container                media objects
Master Mei batches                 optional scheduler              content library
local|docker|both switch           GitHub Actions → GHCR           publish ledger
        \                          /                               /
         \                        /                               /
          +----------------------+-------------------------------+
                                 |
                    WordPress orchestrator bot
                    imports Post Planner xlsx → Facebook
                    Pinterest and YouTube publish from the VM
```

### Oracle VM

The always-on process is the `engine` service in `docker-compose.yml`. GitHub Actions (`.github/workflows/deploy.yml`) builds a `linux/arm64` image, pushes `ghcr.io/diogolean/omni-engine:<git-sha>` and `:latest`, then SSHs to the VM and runs `.github/scripts/remote-deploy.sh`.

That script pins `OMNI_IMAGE_TAG` and starts **engine only**. The scheduler is a Compose profile. Deploy does not start it unless `SCHEDULER_ENABLED` is set. See [0004](decisions/0004-scheduler-not-started-by-deploy.md).

`python -m omni.worker --role engine` stays up so an operator can `docker compose exec engine python main.py ...`. It does not generate content by itself. `--role scheduler` publishes Pinterest image pins on a random 3–6 hour gap.

### Libraries and Backblaze B2

Publishers read the channel library from Google Drive first (`drive.readonly`), then the local tree (the PC `G:` path), then B2. `DRIVE_OUTPUTS_FOLDER_ID` is the shared `outputs` folder, and keys resolve like `OUTPUT_PATH`. One file is downloaded when it is published. See [0007](decisions/0007-google-drive-library-source.md) and `docs/runbooks/google-drive.md`.

B2 remains the publish ledger and the public URL store for Post Planner, which cannot fetch a Drive link. Nothing in the bucket is deleted. See [0001](decisions/0001-b2-on-demand.md) and [0003](decisions/0003-shared-b2-ledger-and-board-dedupe.md). A Drive-only Post Planner asset is uploaded once through the budgeted B2 client (Class A) and left in the bucket.

Every B2 read, list, download, and upload goes through `agents/media/b2_client.py`, which charges `agents/media/b2_budget.py` first. Uploads are Class A and are not counted. The head or list before an upload is Class B or Class C and is counted. The free daily allowance is 1 GB of download, 2,500 Class B, and 2,500 Class C. `B2_DAILY_DOWNLOAD_MB`, `B2_DAILY_CLASS_B`, and `B2_DAILY_CLASS_C` are clamped to that allowance and cannot raise it. A call that would pass a cap raises `B2DailyCapError` and stops the run.

The engine, the scheduler, and scripts on one machine share a file-locked counter at `outputs/b2/usage.json` (override with `B2_USAGE_PATH`). Counters reset at 00:00 UTC. The PC and the Oracle VM do not share a disk, so each keeps its own counter. Defaults stay near half the free allowance (500 MB, 1,500 Class B, 1,500 Class C) so the two hosts together stay inside it. A CI test rejects any non-test module outside `agents/media/` that constructs a boto3 S3 client or a b2sdk API for B2.

### PC runner

GPU and Windows-only work stays on the PC: Aiwake animated episodes (`channels_config/aiwake/` plus `core/animator/`) and Master Mei batches that need the local factory tree. `utils/pipeline_paths.py` still resolves `WINDOWS_FACTORY_ROOT` when the matching env path is unset, so those machines keep their Drive layout.

The runner switch is `local`, `docker`, or `both`. See [0002](decisions/0002-runner-switch-local-docker-both.md).

### WordPress, Facebook, Pinterest, YouTube

| Surface | Who publishes | Source |
|---|---|---|
| Facebook | WordPress orchestrator bot | Post Planner xlsx (`DATE/TIME`, `CAPTION`, `MEDIA URL`) produced by `agents/posting/post_planner.py` |
| Pinterest | VM scheduler | One library image at a time (Drive, then local, then B2), linked to a WordPress article (`core/publishing/pinterest_drip.py`) |
| YouTube | VM, `main.py --publish-youtube` | `private` + `publishAt` via `agents/posting/youtube_publisher.py`. A missing local file is read from Drive. |

Reel rows reach Post Planner and YouTube only when the QA report says `verdict=pass`. See [0005](decisions/0005-fail-closed-reel-qa.md).

## Content path (reel)

1. `engine/orchestrator.py` (entered from `main.py`) drafts a voiceover and synthesizes it (ElevenLabs or the remote GPU voice).
2. `modules/reel_qa/beats.py` cuts the narration into sentence groups of about 4–6 seconds, one still each.
3. `modules/reel_visual_qa.generate_and_gate` draws each still and scores it against that sentence (Qwen via OpenRouter, then Gemini Flash).
4. The sequence compiler burns subtitles and mixes audio into an MP4.
5. `modules/reel_qa/pipeline.seal_compiled_reel` samples frames at subtitle times, checks that audio is present and loud enough, and checks duration ≥ 15s.
6. The report JSON is written beside the MP4 and under `outputs/<page>/qa/`.
7. Post Planner and YouTube read `qa_verdict`. Anything other than `pass` stays unpublished.

The same seal runs for `ECONOMIC_REEL`, `WAN_REEL`, `ECONOMIC_REEL_LOFI`, and `REFERENCE_BASED_REELS`.
