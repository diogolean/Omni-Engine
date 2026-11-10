# Deploy Animind CORE

Goal: host the CORE API so the web generation button can call
`POST /v1/avatars` → `GET /v1/avatars/{id}` end to end. Paid spend stays
blocked until Diogo sets `ANIMIND_AVATAR_SPEND_APPROVED=1` and the ledger
limits.

## Image

Build from the repository root:

```bash
docker build -f deploy/docker/Dockerfile.animind-core -t animind-core .
docker run --rm -p 8787:8787 \
  -e CORE_API_KEY \
  -e ANIMIND_ADMIN_TOKEN \
  -e ANIMIND_DRY_RUN=1 \
  -e ANIMIND_CORS_ORIGINS=https://app.example \
  -e ANIMIND_DATA_DIR=/var/lib/animind-core \
  -v animind-data:/var/lib/animind-core \
  animind-core
```

Healthcheck hits `GET /v1/health` (no API key). The process is ready when
`ok: true`.

## Host sizing

| Resource | Minimum | Notes |
| --- | --- | --- |
| CPU | 4 vCPU | Mock dry-run is light; BiRefNet is the bottleneck |
| RAM | 16 GiB available | Isolated BiRefNet worker; VMs under this OOM-kill the matte |
| GPU | not required | CUDA/ROCm recommended via `ANIMIND_MATTE_DEVICE` |

Dry-run uses `build_mock_rig` and does not load BiRefNet. Paid
`create_character → build_rig` always does.

Measure on the target host:

```bash
python -m products.animind_core.capacity --out /tmp/animind-capacity.json
```

## Environment names only

Never put secret values in git. Names the host must provide:

- `CORE_API_KEY`
- `CORE_PUBLIC_URL`
- `ANIMIND_DATA_DIR`
- `ANIMIND_CORS_ORIGINS`
- `ANIMIND_DRY_RUN`
- `ANIMIND_FORCE_MOCK`
- `ANIMIND_GENERATION`
- `GEMINI_API_KEY` or `GOOGLE_API_KEY`
- `ANIMIND_ADMIN_TOKEN`
- `ANIMIND_SUPER_ADMIN_TOKEN`
- `ANIMIND_AVATAR_SPEND_APPROVED`
- `ANIMIND_SPEND_DATABASE_URL`
- `ANIMIND_SPEND_BUDGET_CENTS`
- `ANIMIND_SPEND_USER_CENTS`
- `ANIMIND_SPEND_REQUEST_CENTS`
- `ANIMIND_SPEND_TIMEOUT_MS`
- `ANIMIND_SPEND_MAX_RETRIES`
- `ANIMIND_SPEND_RATE_PER_MIN`
- `ANIMIND_SPEND_CONCURRENCY`
- `ANIMIND_SPEND_MAX_INPUT_TOKENS`
- `ANIMIND_SPEND_MAX_OUTPUT_TOKENS`
- `ANIMIND_SPEND_MAX_TOTAL_TOKENS`
- `ANIMIND_SPEND_MAX_IMAGES`
- `ANIMIND_SPEND_MAX_AUDIO_SEC`
- `ANIMIND_SPEND_MAX_VIDEO_SEC`
- `ANIMIND_PRICE_TABLE_JSON` (only for non-built-in models)
- `ANIMIND_MATTE_MODE`
- `ANIMIND_MATTE_DEVICE`
- `ANIMIND_MATTE_TIMEOUT_S`

Official Gemini rates for `gemini-2.5-flash` and `gemini-2.5-flash-image`
are built into CORE. Unknown cost stays `null`, never zero.

## Auth for the web

The browser never holds admin or spend tokens.

1. Web server authenticates the logged-in account.
2. Web server calls CORE with `Authorization: Bearer $CORE_API_KEY`.
3. Web server sends `X-Animind-User` as an opaque owner id.
4. Only after the account is admin/super-admin does the web server attach
   `X-Animind-Admin` with the server-side token.
5. Generation also needs `Idempotency-Key` and `X-Request-Id`.
6. `X-Animind-Role` is ignored by CORE.

CORS: set `ANIMIND_CORS_ORIGINS` to the web origin(s). Empty means no CORS
middleware (server-to-server only).

## Dry-run mode

With `ANIMIND_DRY_RUN=1`, an admin may call:

```http
POST /v1/avatars
{"name":"Ada","prompt":"...","generate":true,"dryRun":true}
```

This forces the mock provider, skips paid spend admission, persists the
`animind.puppet.v3` document, and returns the same body from
`GET /v1/avatars/{id}`. No Gemini socket is opened.

CLI equivalent:

```bash
python -m products.animind_core.dry_run --out /tmp/animind-dry-run
```

## Paid button (blocked until approval)

Keep `ANIMIND_AVATAR_SPEND_APPROVED` unset/empty until Diogo approves the
ceiling. Preflight without spending:

```http
POST /v1/admin/avatars/preflight
```

Exact blockers are listed in `missing`. Contract details:
`products/animind_core/docs/avatar-contract.md`.
