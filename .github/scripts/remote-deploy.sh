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

mkdir -p credentials outputs
touch releases.log
printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${IMAGE_TAG}" >> releases.log

cd /opt/omni-engine && docker compose pull && docker compose up -d
docker compose ps
