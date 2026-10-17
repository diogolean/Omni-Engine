#!/bin/bash
# Runs on the Oracle VM over SSH. IMAGE_TAG is the git SHA (or "latest").
# Does not print .env. Requires /opt/omni-engine/.env to already exist.
set -euo pipefail

cd /opt/omni-engine

if [[ ! -f .env ]]; then
  echo "Missing /opt/omni-engine/.env — copy secrets there once before the first deploy." >&2
  exit 1
fi

if [[ ! "${IMAGE_TAG:-}" =~ ^(latest|[0-9a-f]{7,40})$ ]]; then
  echo "Refusing image tag: ${IMAGE_TAG:-<empty>}" >&2
  exit 1
fi

if grep -q '^OMNI_IMAGE_TAG=' .env; then
  sed -i "s/^OMNI_IMAGE_TAG=.*/OMNI_IMAGE_TAG=${IMAGE_TAG}/" .env
else
  printf '\nOMNI_IMAGE_TAG=%s\n' "${IMAGE_TAG}" >> .env
fi

mkdir -p credentials outputs \
  channels_config/ancient_knowledge \
  channels_config/anna_protocol
touch releases.log
printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${IMAGE_TAG}" >> releases.log

# Deploy starts the engine only. The scheduler publishes pins, so it stays
# stopped unless SCHEDULER_ENABLED is exactly 1/true/yes/on in .env.
# Do not print .env.
enabled=0
if grep -qE '^SCHEDULER_ENABLED=(1|true|TRUE|yes|YES|on|ON)[[:space:]]*$' .env; then
  enabled=1
fi

cd /opt/omni-engine
if [[ "$enabled" == 1 ]]; then
  echo "SCHEDULER_ENABLED is set — starting engine and scheduler"
  docker compose --profile scheduler pull
  docker compose --profile scheduler up -d
else
  echo "Starting engine only. Scheduler stays stopped (set SCHEDULER_ENABLED=1 to start it)."
  docker compose pull engine
  docker compose up -d engine
  docker compose --profile scheduler stop scheduler || true
fi
docker compose ps
