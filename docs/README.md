# Docs

One index. The root [README](../README.md) is the short reviewer page.

## Start here

| Doc | What it answers |
|---|---|
| [architecture/overview.md](architecture/overview.md) | Where the factory runs, and how a reel reaches Facebook, Pinterest, and YouTube |
| [architecture/diagrams.md](architecture/diagrams.md) | Architecture, reel and pin pipelines, workers, and the Validate-then-Deploy flow |
| [architecture/REPO_LAYOUT.md](architecture/REPO_LAYOUT.md) | What may live at the repo root, and what stays out of git |
| [architecture/main-module-map.md](architecture/main-module-map.md) | Where `main.py` hands off into `production/pipeline/` |
| [setup.md](setup.md) | Python, ffmpeg, `.env` |
| [deploy.md](deploy.md) | Oracle VM, GHCR, rollback, Ancient Knowledge store mount |
| [COMMAND_REFERENCE.md](COMMAND_REFERENCE.md) | CLI flags and copy-paste commands |

## Runbooks

| Doc | |
|---|---|
| [runbooks/google-drive.md](runbooks/google-drive.md) | Drive as the library source |
| [runbooks/supermemory.md](runbooks/supermemory.md) | Opt-in memory service |
| [elevenlabs_tts.md](elevenlabs_tts.md) | TTS speed lives inside `voice_settings` |

## Decisions

Records in [architecture/decisions/](architecture/decisions/):

| | |
|---|---|
| [0001](architecture/decisions/0001-b2-on-demand.md) | B2 on demand |
| [0002](architecture/decisions/0002-runner-switch-local-docker-both.md) | PC runner switch |
| [0003](architecture/decisions/0003-shared-b2-ledger-and-board-dedupe.md) | Shared B2 ledger |
| [0004](architecture/decisions/0004-scheduler-not-started-by-deploy.md) | Deploy does not start the scheduler |
| [0005](architecture/decisions/0005-fail-closed-reel-qa.md) | Fail-closed reel QA |
| [0006](architecture/decisions/0006-central-supermemory.md) | Supermemory on the VM |
| [0006 reel grounding](architecture/decisions/0006-reel-beat-image-grounding.md) | Beat image grounding |
| [0007](architecture/decisions/0007-google-drive-library-source.md) | Google Drive library |
| [0008](architecture/decisions/0008-target-architecture.md) | Target layers: production, distribution, animation, products |
| [ADR: free images first](architecture/ADR-free-images-first.md) | Free images before generated stills |

Showcase posters live in `portfolio_showcase/`, not here. See [architecture/DEPRECATED.md](architecture/DEPRECATED.md).

The pre-move agent writeup is [archive/AGENT_ARCHITECTURE.md](archive/AGENT_ARCHITECTURE.md). It names the old folders. Those imports are deprecated shims until M14.
