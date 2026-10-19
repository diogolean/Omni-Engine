# Deploy

Target: Oracle Cloud VM, Ubuntu 24.04, aarch64, checkout `/opt/omni-engine`. The image is `linux/arm64` at `ghcr.io/diogolean/omni-engine`, tagged with the git SHA and `latest`. How the pieces fit: [architecture/overview.md](architecture/overview.md).

A push to `main` runs `.github/workflows/deploy.yml`. It builds the image, pushes it, copies `docker-compose.yml` to the VM, sets `OMNI_IMAGE_TAG`, and starts the **engine** service only:

```bash
cd /opt/omni-engine && docker compose pull engine && docker compose up -d engine
```

The job name on pull requests stays `lint and tests` (`.github/workflows/validate.yml`). Pull requests do not deploy.

## What compose mounts

`docker-compose.yml` is unchanged by the docs cleanup. The engine and scheduler mount:

- `./outputs` and `./credentials`
- `./.env` read-write, so a token refresh persists
- `./channels_config` at `channels_env/` inside the container, beside the image's channel package
- `./state/ancient_knowledge/store` onto `channels_config/ancient_knowledge/store`

That last mount is the Ancient Knowledge store only. Aiwake's committed store stays in the image. The worker entry is `python -m core.worker` (compose and the Dockerfile). The `$OUTPUT_PATH/_omni` ledger and the `OMNI_*` env names do not change.

The scheduler is Compose profile `scheduler`. Deploy does not start it unless `SCHEDULER_ENABLED=1`. Supermemory is profile `memory` and is also not started by deploy.

## Local image

```bash
docker compose up -d --build engine
docker compose exec engine python main.py --channel wonder_feed --post-type ECONOMIC_REEL --quantity 1
```

Dry-run one Pinterest wake. No Pinterest call, no ledger write:

```bash
docker compose --profile scheduler run --rm --no-deps scheduler \
  python -m core.worker --role scheduler --once --dry-run
```

Cards land in `outputs/pinterest/dryrun/`. One wake posts `PINTEREST_PINS_PER_RUN` image pins (default 1) for each ready account in `config/pinterest_accounts.yaml`.

## GitHub secrets

| Secret | Value |
|---|---|
| `VM_HOST` | VM public IP or DNS |
| `VM_USER` | SSH user in the `docker` group |
| `VM_SSH_KEY` | Private key for that user |

`GITHUB_TOKEN` pushes the image. The first push creates a private package. On the VM, as `VM_USER`, log in once with a PAT that has `read:packages`:

```bash
echo '<PAT>' | docker login ghcr.io -u diogolean --password-stdin
```

## One-time VM directories

```bash
sudo mkdir -p /opt/omni-engine/outputs /opt/omni-engine/credentials/tokens \
  /opt/omni-engine/state/ancient_knowledge/store
sudo chown -R "$USER":"$USER" /opt/omni-engine
```

YouTube tokens live at `credentials/tokens/youtube_token_<page>.json`. Channel env files live at `channels_config/<channel>/.env`. Neither is committed.

## Rollback

`releases.log` and `OMNI_IMAGE_TAG` in the VM `.env` record the SHA. From GitHub: Actions → Deploy → Run workflow, and set the image tag to that SHA. The workflow skips the build and pulls that tag.

On the VM:

```bash
cd /opt/omni-engine
# set OMNI_IMAGE_TAG to the previous SHA
docker compose pull engine && docker compose up -d engine
```
