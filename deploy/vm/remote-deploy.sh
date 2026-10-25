#!/bin/bash
# Runs on the Oracle VM over SSH. IMAGE_TAG is a 40-character git SHA or "latest".
# Does not print .env. Requires /opt/omni-engine/.env to already exist.
# OMNI_IMAGE_TAG is written only after the image pull succeeds, so a failed
# pull leaves the previous tag in place.
set -euo pipefail

root="${OMNI_DEPLOY_ROOT:-/opt/omni-engine}"
cd "$root"

if [[ ! -f .env ]]; then
  echo "Missing ${root}/.env — copy secrets there once before the first deploy." >&2
  exit 1
fi

if [[ ! "${IMAGE_TAG:-}" =~ ^(latest|[0-9a-f]{40})$ ]]; then
  echo "Refusing image tag: ${IMAGE_TAG:-<empty>}" >&2
  exit 1
fi

mkdir -p credentials outputs
# CHANNEL_DIRS is computed on the runner from VM_ENSURE_CHANNEL_DIR and
# VM_STATE_MOUNT. The VM does not import the app to discover them.
# Re-check every path before creating anything, and pass each one quoted.
if [[ -n "${CHANNEL_DIRS:-}" ]]; then
  read -r -a _channel_paths <<< "${CHANNEL_DIRS}"
  for rel in "${_channel_paths[@]}"; do
    if ! [[ "$rel" =~ ^(channels_config|state)/[a-z0-9_]+(/store)?$ ]]; then
      echo "Refusing channel path: ${rel}" >&2
      exit 1
    fi
  done
  for rel in "${_channel_paths[@]}"; do
    mkdir -p "$rel"
  done
fi

# Deploy starts the engine only. The scheduler publishes pins, so it stays
# stopped unless SCHEDULER_ENABLED is exactly 1/true/yes/on in .env.
# The memory profile is not selected here, so Supermemory is not started.
# Do not print .env.
enabled=0
if grep -qE '^SCHEDULER_ENABLED=(1|true|TRUE|yes|YES|on|ON)[[:space:]]*$' .env; then
  enabled=1
fi

# Compose interpolates OMNI_IMAGE_TAG from the environment, which wins over
# the file. The file is updated only after this pull returns success.
if [[ "$enabled" == 1 ]]; then
  echo "SCHEDULER_ENABLED is set — pulling engine and scheduler"
  OMNI_IMAGE_TAG="${IMAGE_TAG}" docker compose --profile scheduler pull
else
  echo "Pulling the engine image."
  OMNI_IMAGE_TAG="${IMAGE_TAG}" docker compose pull engine
fi

if grep -q '^OMNI_IMAGE_TAG=' .env; then
  sed -i "s/^OMNI_IMAGE_TAG=.*/OMNI_IMAGE_TAG=${IMAGE_TAG}/" .env
else
  printf '\nOMNI_IMAGE_TAG=%s\n' "${IMAGE_TAG}" >> .env
fi
touch releases.log
printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${IMAGE_TAG}" >> releases.log

cd "$root"
if [[ "$enabled" == 1 ]]; then
  echo "SCHEDULER_ENABLED is set — starting engine and scheduler"
  docker compose --profile scheduler up -d
else
  echo "Starting engine only. Scheduler stays stopped (set SCHEDULER_ENABLED=1 to start it)."
  docker compose up -d engine
  docker compose --profile scheduler stop scheduler || true
fi
docker compose ps
