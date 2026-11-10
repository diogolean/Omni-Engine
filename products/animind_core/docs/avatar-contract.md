# Avatar generation contract

The CORE server is the only production authority for generation, limits, and
money. The web sends commands and renders the returned document; it must not
reserve, estimate, or charge locally.

## Server authentication and ownership

All routes require `Authorization: Bearer $CORE_API_KEY`.

`POST /v1/avatars` with `generate: true` additionally requires:

- `X-Animind-User`: opaque owner id; blank, email, or invalid ids are refused.
- `X-Animind-Admin`: either server-only `ANIMIND_ADMIN_TOKEN` or
  `ANIMIND_SUPER_ADMIN_TOKEN`. A browser `X-Animind-Role` is ignored.
- `Idempotency-Key`: 8–80 URL-safe characters, bound to owner and request body.
- `X-Request-Id`: 8–80 URL-safe characters for request/call correlation.

Admin and super-admin can generate. User, visitor, and anonymous callers
cannot. `GET /v1/admin/spend` accepts only the separate super-admin token.
Tokens are server secrets and must never be exposed to browser JavaScript.

## POST /v1/avatars

```json
{
  "generate": true,
  "name": "one character name",
  "prompt": "one character visual description",
  "language": "pt",
  "reuse": false
}
```

One request generates exactly one character. The canonical execution path is
`generate_avatar` → `characters.create_character` → `rig.build_rig`; it uses
the existing modular Aiwake/Animind factory, detector, matte, compositor, QA,
expression sheets, and eye controls. It does not create another factory.

The response and the document persisted for GET use
`schema: "animind.puppet.v3"`:

- `id`, `name`, `owner`, `generator`
- `status`: `ready` only when every gate passes, otherwise
  `review_required`; blocking pre-generation errors are HTTP errors
- `ready`, `review_required`, `defects`, `qa`
- `views.front|left|right|profile`, each with `canvas`, `layers`, `anchors`,
  `visemes`, `expressions`, `eyeRig`, `browModel`, and provenance
- `camera`: common canvas/eye-line/head/shoulder framing contract
- `scenario`: `{embedded:false,...}`; background is separate and replaceable
- `expressionContract`: neutral, rests, X/A–H visemes, lids, gaze, and brows
- `provenance`, including real/generated/reused view status and resolution
- `stages.actualModules` and `stages.architecturalSources`
- `callPlan`: maximum image/detection calls and retry policy
- `usage`: `animind.usage.v1`, the only monetary result

The base face is mouthless and feature layers are independent. Brows are
assembled after face, mouth, eyes, lids, and gaze per view. A missing profile,
mirrored/reused view, opaque head, fixture source, bad neck/head junction, or
failed QA remains inspectable with `review_required`; it is never mislabeled
ready and is not deleted.

## GET /v1/avatars/{id}

Requires `Authorization` and the same `X-Animind-User` owner. It returns the
exact final POST document, including `usage`, or owner-safe `404`. GET does not
reserve or add cost.

## Idempotency, attempts, and limits

An outer idempotency replay returns the stored resource without provider
calls. Inside a request, every provider attempt has a deterministic identity
from idempotency/request, provider, model, operation, prompt digest, and
attempt number. Different factory stages are counted separately; the same
attempt cannot reserve twice. Holds are transactional in Postgres.

Required fail-closed settings include global budget, UTC per-user/day budget,
per-request budget (`ANIMIND_SPEND_REQUEST_CENTS`), per-minute rate,
concurrency, timeout, input/output token caps, image cap, and retry cap. The
full current plan is at most 18 image calls and 6
`detect_features` calls; provider-internal retries are disabled.

## Matte backend

BiRefNet uses the existing matte extension. `ANIMIND_MATTE_MODE=isolated`
(default) runs a persistent spawned worker so an OS OOM kill does not kill the
API process. `ANIMIND_MATTE_DEVICE=cpu|cuda|rocm|auto` selects an available
ONNX provider and `ANIMIND_MATTE_TIMEOUT_S` defaults to 180 seconds.

CPU mode requires a dedicated worker with at least 16 GiB available memory.
The observed 14.6 GiB VM could not fit the model. Unsupported devices,
timeouts, worker exits, and OOM/SIGKILL fail explicitly. There is no silent
chroma fallback.

## Controlled real proof (prepared, not executed)

Call `POST /v1/admin/avatars/preflight` with CORE auth and an admin or
super-admin server token. It performs no provider call and reports models,
official prices, exact call plan, configured caps, worst-case request hold,
and every blocking prerequisite. When `ready: true`, execute the single
character body above through `POST /v1/avatars`.

The proof remains blocked until all of these are provisioned explicitly:

1. `ANIMIND_GENERATION=1`
2. `GEMINI_API_KEY` or `GOOGLE_API_KEY`
3. reachable `ANIMIND_SPEND_DATABASE_URL`
4. concrete global and UTC user/day budgets, rate, concurrency, timeout,
   token, 18-image, and two-retry limits
5. official published model rows below
6. `ANIMIND_AVATAR_SPEND_APPROVED=1`, set only after Diogo approves this run
7. admin/super-admin server token, owner, idempotency key, and request id

No proof or paid provider call is run merely by preflight.

## Official Gemini prices

Source checked 2026-11-08:
<https://ai.google.dev/gemini-api/docs/pricing>.

- `gemini-2.5-flash`: input $0.30/1M tokens, cache $0.03/1M,
  output including thinking $2.50/1M.
- `gemini-2.5-flash-image`: input text/image $0.30/1M tokens plus image
  output $0.039/image up to 1024×1024 (1290 output tokens).

$0.039 is not an all-in image price. Image success cost stays `null` unless
both image count and input-token usage are observed. Detection and retries are
separate calls. Unknown estimated or reported cost is `null`, never zero.
The official page marks these models deprecated; model replacement and its
official price must be approved and published before execution.


## Dry-run

Set `ANIMIND_DRY_RUN=1` on the server. An admin may then call
`POST /v1/avatars` with `{"generate":true,"dryRun":true}`. CORE forces the
mock provider, skips paid spend admission, persists `animind.puppet.v3`, and
returns the same document from `GET /v1/avatars/{id}`. No Gemini socket opens.

Hosting, CORS, healthcheck, and env names:
`products/animind_core/docs/deploy.md`.
