# Supermemory on the VM

The memory service is opt-in. A deploy does not start it, and it does not publish a port. The engine container reaches it at `http://supermemory:6767`. See `docs/architecture/decisions/0006-central-supermemory.md`.

Server pin: **0.0.9** (linux-arm64). Do not run 0.0.8 or older on the VM. Those builds listen on every interface without an API key.

## Before the first start

Both of these need to be on `main`, and the GHCR images need to exist:

- the shared memory client (so the engine speaks API v3 to any host)
- this service image (`ghcr.io/diogolean/supermemory-server:0.0.9`)

On the VM, `/opt/omni-engine/.env` (mode 600, not in git) must contain a Gemini key under the name Omni-Engine already uses:

- `GEMINI_API_KEY`, or
- `GOOGLE_API_KEY` (the container copies it to `GEMINI_API_KEY` and does not print it)

`SUPERMEMORY_API_KEY` is filled in after the first boot. Leave it empty until then.

## Start

```bash
cd /opt/omni-engine
docker compose --profile memory pull supermemory
docker compose --profile memory up -d supermemory
```

The first boot downloads the local embedding model into the volume. Give it a few minutes.

## API key and smoke test

Health does not print secrets:

```bash
docker compose --profile memory exec -T supermemory \
  curl -fsS http://127.0.0.1:6767/v3/health
```

Read the generated key once and append it to `.env` as `SUPERMEMORY_API_KEY`. Do not commit that line, and do not paste the key into chat or logs.

```bash
docker compose --profile memory exec -T supermemory cat /data/api-key
chmod 600 .env
```

Restart the engine so it picks up the key, then check status only (the command prints a number, not the key):

```bash
docker compose up -d engine
docker compose exec -T engine python -c 'import os,urllib.request; req=urllib.request.Request("http://supermemory:6767/v3/projects", headers={"Authorization":"Bearer "+os.environ["SUPERMEMORY_API_KEY"]}); print(urllib.request.urlopen(req, timeout=5).status)'
```

`200` means the engine can see the service on the compose network.

## SSH tunnel from the workstation

The committed compose file does not publish a port. For a tunnel, add a file that is **not** named `docker-compose.override.yml` (Compose would load that on every `up`, including deploy). Keep it only on the VM:

`docker-compose.memory-tunnel.yml`

```yaml
services:
  supermemory:
    ports:
      - "127.0.0.1:6767:6767"
```

```bash
docker compose --profile memory \
  -f docker-compose.yml -f docker-compose.memory-tunnel.yml \
  up -d supermemory
```

From the PC:

```bash
ssh -L 6767:127.0.0.1:6767 <user>@<vm>
```

Point the workstation at the tunnel with `SUPERMEMORY_BASE_URL=http://127.0.0.1:6767` and the same `SUPERMEMORY_API_KEY`. The Windows WSL launch stays off unless `SUPERMEMORY_AUTOSTART_WSL=1`.

## Re-seed Aiwake

The PC runs server 0.0.8. The VM runs 0.0.9. Both speak API v3 (container tags, `/v3/documents`, `/v3/search`). The 5.x SDK does not. Do **not** copy `~/.supermemory` or the volume between those versions. The encryption identity and store layout travel with the directory. Seed from the local files instead.

On the VM, after the key is in `.env` and the engine has been restarted:

```bash
docker compose exec -T engine \
  python -m products.aiwake.tools.seed_supermemory
```

That reads approved sessions and writes `aiwake_production_history` plus `aiwake_system_rules`. It is sequential, which suits a 2-core VM. Run it again any time; a session already present is skipped.

`SUPERMEMORY_REQUIRED` stays unset. If the service is down, production keeps going on the local ledger. Set `SUPERMEMORY_REQUIRED=1` only when an offline service should stop a production run.

## Backup

The backup is not scheduled. No cron on the VM calls `backup_supermemory.sh`. Deploy copies `deploy/vm/backup_supermemory.sh` to `/opt/omni-engine/scripts/backup_supermemory.sh` and does not run it. Until scheduling is decided, run it by hand on the VM host:

```bash
/opt/omni-engine/scripts/backup_supermemory.sh
```

The shell script archives the volume to `outputs/backups/supermemory/YYYY-MM-DD.tgz`. If the container was running it stops it for the tar and starts it again. If it was stopped, it stays stopped. The engine container then uploads `backups/supermemory/YYYY-MM-DD.tgz`. An object that already exists for that day is left as it is. Nothing in B2 is deleted. The upload shares the daily B2 budget; when the budget is spent the local archive stays and the script exits 2. Seven local archives are kept.

The upload module is `tools/backup_supermemory.py`. The shell script still runs `python scripts/backup_supermemory.py`, which is a shim until M14.

## Restore

1. Stop the service: `docker compose --profile memory stop supermemory`.
2. Download the chosen `backups/supermemory/YYYY-MM-DD.tgz` from B2. Do not delete the object.
3. Copy the current volume aside (`docker run --rm -v omni-engine_supermemory-data:/data -v "$PWD:/backup" ghcr.io/diogolean/supermemory-server:0.0.9 tar -C /data -czf /backup/pre-restore.tgz .`) before replacing it.
4. Clear and extract into the volume with the same image, entrypoint `tar`, and the archive mounted read-only.
5. Start the service again and repeat the health and projects checks above.
6. Do not point a 0.0.8 binary at this volume. If the archive is bad, seed again from the local session files instead of copying a workstation data directory.
