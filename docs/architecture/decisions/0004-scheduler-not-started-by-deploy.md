# ADR 0004 — Deploy does not start the scheduler

## Status

Accepted

## Context

The scheduler posts real Pinterest pins. A push to `main` rebuilds the image. Starting the scheduler on every deploy would publish during a rollback, a config typo, or a half-ready account list.

## Decision

`deploy/vm/remote-deploy.sh` starts the `engine` service only. M12 moved it out of `.github/scripts/`.

The scheduler stays stopped unless `/opt/omni-engine/.env` contains `SCHEDULER_ENABLED=1` (also `true`, `yes`, or `on`). An operator can also start it by hand:

```bash
docker compose --profile scheduler up -d scheduler
```

`deploy/compose/docker-compose.yml` documents the same rule: the `scheduler` service is behind the `scheduler` profile and is not part of a normal `up`. The workflow copies that file to `/opt/omni-engine/docker-compose.yml`.

## Consequences

- A deploy updates the image the engine will use and leaves publishing off.
- Turning publishing on is a deliberate edit of the VM env, or an explicit Compose command.
- Pull requests do not deploy. `.github/workflows/validate.yml` runs checks only.
