#!/bin/bash
# Manual Supermemory backup on the VM host.
# Not scheduled. No cron calls this script. Run it by hand until scheduling is decided.
# Stops the container only when it is already running, then starts it again.
# Does not start it when it was stopped. Does not delete anything in B2.
# The upload runs in the engine container so it shares the daily B2 budget.
set -euo pipefail

cd /opt/omni-engine

day="$(date -u +%Y-%m-%d)"
backup_dir="outputs/backups/supermemory"
mkdir -p "$backup_dir"

image="${SUPERMEMORY_IMAGE:-ghcr.io/diogolean/supermemory-server:0.0.9}"
volume="${SUPERMEMORY_VOLUME:-omni-engine_supermemory-data}"

running="$(docker compose --profile memory ps --status running -q supermemory || true)"
if [ -n "$running" ]; then
  docker compose --profile memory stop supermemory
fi

status=0
docker run --rm --entrypoint tar \
  -v "${volume}:/data:ro" \
  -v "$PWD/${backup_dir}:/backup" \
  "$image" \
  -C /data -czf "/backup/${day}.tgz" . || status=$?

if [ -n "$running" ]; then
  docker compose --profile memory up -d supermemory
fi

if [ "$status" -ne 0 ]; then
  echo "supermemory archive failed" >&2
  exit "$status"
fi

docker compose exec -T engine \
  python scripts/backup_supermemory.py --upload-only --day "$day" \
  --backup-dir "/opt/omni-engine/${backup_dir}"
