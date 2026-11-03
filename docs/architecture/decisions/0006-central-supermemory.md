# ADR 0006 — Central Supermemory on the VM

## Status

Accepted

## Context

Aiwake already stores production history in a self-hosted Supermemory server. That server runs on the workstation as `supermemory-server` 0.0.8. The Oracle VM (aarch64, 2 cores, 11 GB RAM) is where the engine container runs. Memory should live next to the engine, and the workstation should still be able to reach it.

Server 0.0.8 and older bind every interface and do not require an API key. Server 0.0.9 binds `127.0.0.1` by default and requires the key on every request. It still serves API v3 (`/v3/health`, `/v3/documents`, `/v3/search`, container tags). The 5.x SDK speaks `/ns/{namespace}` and needs 0.0.9 or later, so the shared client stays on the 3.x SDK.

Copying `~/.supermemory` from 0.0.8 into a 0.0.9 volume is not a migration. The store and its encryption identity are tied to the server that wrote them. Approved sessions already live in local files (`history_seeds.json` and the content library).

## Decision

- Run `supermemory-server` 0.0.9 in an arm64 image, `ghcr.io/diogolean/supermemory-server`, built by the existing GHCR workflow. The Dockerfile downloads the pinned linux-arm64 binary and checks its SHA-256.
- The compose service is behind the `memory` profile. It publishes no host port. Inside the container it listens on `0.0.0.0:6767` so other containers can reach `http://supermemory:6767`. The engine service sets that URL. A workstation uses its own `SUPERMEMORY_BASE_URL` (localhost or an SSH tunnel).
- Data is the named volume `supermemory-data`, mounted at `/data`. Memory limit 2 GB, one CPU.
- `SUPERMEMORY_API_KEY` and the Gemini key stay in the git-ignored `/opt/omni-engine/.env`. The container maps `GOOGLE_API_KEY` to `GEMINI_API_KEY` when only the Google name is set. Keys are not printed.
- Deploy starts the engine only. It does not pass the `memory` profile, and it does not start the scheduler unless `SCHEDULER_ENABLED` is set.
- Move workstation data by re-seeding from local files. Do not copy the 0.0.8 data directory.
- A nightly host script archives the volume to one B2 object, `backups/supermemory/YYYY-MM-DD.tgz`. It never deletes or overwrites an object already in B2, and it uses the same daily B2 budget as the rest of the engine. Seven local archives are kept.

## Consequences

- The service stays off until someone starts `--profile memory` after the image exists and `.env` has the Gemini key.
- Losing the volume loses the graph. The ledger files and the B2 archive are the recovery path.
- The workstation on 0.0.8 can keep talking v3 to either its local server or the tunnel. It cannot mount the VM data directory.
- LLM extraction on each add uses the Gemini key already configured for Omni-Engine. Embeddings stay local.
