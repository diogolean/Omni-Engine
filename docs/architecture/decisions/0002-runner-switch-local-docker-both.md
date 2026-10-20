# ADR 0002 — Runner switch: local, docker, or both

## Status

Accepted

## Context

Some jobs need a Windows checkout and a GPU (Aiwake animated episodes, Master Mei batches). The Oracle VM is the right place for the always-on engine, Pinterest drip, and YouTube scheduling. A few steps are useful on either machine.

## Decision

Every job class picks one runner mode:

| Mode | Where it runs | Examples |
|---|---|---|
| `local` | PC runner | Aiwake animated render (`channels_config/aiwake/`, `core/animator/`), Master Mei batches, anything that still resolves `WINDOWS_FACTORY_ROOT` |
| `docker` | Oracle VM containers | `distribution.workers --role engine`, Pinterest scheduler, YouTube `publishAt` from `main.py` |
| `both` | Either runner, same B2 objects | Upload a finished MP4, read the library, append the publish ledger |

There is no single `RUNNER=` environment variable yet. The mode is expressed by where the operator starts the process, plus:

- Compose service `engine` versus profile `scheduler` (`docker-compose.yml`)
- `ENABLE_REMOTE_GPU_WORKFLOWS` when a docker or local process should send image, audio, or video to the remote GPU
- `WINDOWS_FACTORY_ROOT` in `utils/pipeline_paths.py` when a PC path is still required

## Consequences

- GPU work is not forced through the arm64 VM image.
- `both` jobs must read and write B2 (or the mounted `outputs/` volume that the ledger lives on), not a private disk path.
- Adding the enum as a real flag can wait until a job needs to choose the mode without an operator picking the machine.
