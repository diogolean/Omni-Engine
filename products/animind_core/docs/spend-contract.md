"""Animindly spend contract for the web app.

The Next.js app (github.com/diogolean/animind, `feat/mvp-ui`) reserves one
outer call per route. It does not see provider calls made inside this core.
This document is the compatibility report. The web repo does not need a change
for the core to start metering those calls.

## Response field

`POST /v1/plan`, `POST /v1/render`, and `GET /v1/jobs/{id}` add `usage`:

```json
{
  "schema": "animind.usage.v1",
  "requestId": "req_…",
  "jobId": null,
  "userRef": "24 hex chars, not an email",
  "calls": [
    {
      "provider": "deepseek",
      "model": "deepseek-flash",
      "attempt": 1,
      "status": "succeeded",
      "usage": {"inputTokens": 1000, "outputTokens": 500, "cachedTokens": 200, "thinkingTokens": null},
      "units": {"images": null, "audioSeconds": null, "videoSeconds": null},
      "cost": {
        "currency": "USD",
        "estimatedMicro": 842,
        "reportedMicro": null,
        "status": "estimated",
        "priceVersion": "deepseek-peak-2026-11-07",
        "holdCents": 1
      }
    }
  ],
  "totals": {
    "estimatedMicro": 842,
    "reportedMicro": null,
    "estimatedUnknown": 0,
    "reportedUnknown": 1,
    "holdCents": 1
  }
}
```

`estimatedMicro` and `reportedMicro` are micro-USD (1 USD = 1,000,000). Null means
unknown. A missing provider bill is not stored as zero. `holdCents` is only the
ceiling counted against the cap, rounded up to at least 1 cent when the
estimate is positive and below 1 cent. It is not the measured bill.

`GET /v1/jobs/{id}` and `GET /v1/files/…` do not reserve. Polling reads rows
already stored for that job. A job that exists only in the ledger after a
restart returns `status: "unknown"` plus `usage`. The MP4 itself is still in
process memory.

## What the web should do later

Map `estimatedMicro` with `ceil(micro / 10000)` cents if it attaches usage to
its own ledger. Do not add a second reservation when `calls` is empty or when
the request is a job poll or a file download. `scriptCost.costUsd` on the
legacy render result is not this contract. Offline template rows still say
`0.0` there; `usage` is the source of truth and uses null when no provider
priced the call.

Send `X-Animind-User` as an opaque id (`[A-Za-z0-9_-]{1,80}`). An email is
rejected and not stored. Without the header every caller shares the
`anonymous` cap. `X-Animind-Role: admin` does not skip the global cap.
`Idempotency-Key` is bound to that user and a hash of the JSON body. The body
is not written to Postgres. The same key with a different body is `409`.

## Prices verified for this change

Checked 2026-11-07 at <https://api-docs.deepseek.com/quick_start/pricing>.
The page names `deepseek-flash` as DeepSeek-V4.1-Flash-1017 and
`deepseek-v4-pro` as DeepSeek-V4-Pro-0813. Estimates use the peak column
because off-peak is half and the page says prices can change.

| Model | Cache-miss input | Cache-hit input | Output |
| --- | --- | --- | --- |
| `deepseek-flash` | $0.30 / 1M | $0.006 / 1M | $1.20 / 1M |
| `deepseek-v4-pro` | $1.32 / 1M | $0.044 / 1M | $3.96 / 1M |

No other provider has a rate in this tree from a page fetched for this change.
Gemini, Together, OpenRouter, DashScope, and fal calls are refused until
`ANIMIND_PRICE_TABLE_JSON` supplies a sourced row. A zero price is rejected.
Prices from `production/cost` and from other products are not copied.

## Limits

`ANIMIND_GENERATION` must be exactly `1`. Any other value, or a missing limit
or database URL, refuses the provider call before the socket opens. The global
budget includes admin. The ledger is Postgres (`ANIMIND_SPEND_DATABASE_URL`),
with one transaction around the row lock, the hold, and the insert. There is
no in-memory fallback.

## Coverage

| Path | Meter | Price |
| --- | --- | --- |
| DeepSeek chat (`llm_providers.DeepSeekProvider`) | input, output, cache when the response has `prompt_cache_hit_tokens` | verified peak |
| Gemini text (`script._gemini_call`) | tokens when `usage_metadata` is present, else null | unavailable, blocked |
| OpenRouter text | tokens; `usage.cost` is reported only | unavailable unless the operator table has the model |
| Together / OpenRouter / Gemini images | image count | unavailable unless the operator table has the model |
| DashScope and fal adapters | no socket; the guard refuses when a request frame is active | unavailable |
| edge-tts retries | attempt count, cost null, reason `unpriced_transport` | not a keyed API |
| Local script cache | `not_charged`, tokens null, reason `cache` | no provider call |
| `POST /v1/plan` | no provider call | planner is local |
| `GET /v1/jobs/{id}`, `GET /v1/files/…` | no new row | unbilled read |

No streaming client is used. DeepSeek and OpenRouter send `stream: false`.
A `stream: true` request is refused before the socket opens. The render worker
is a thread in this process; it copies the request frame. There is no separate
queue worker. `python -m` entry points for `bench`, `benchmark`, and `avatar`
open the same guard. If `ANIMIND_GENERATION` is set, a direct provider call
is guarded even without an HTTP frame. Adapter tests that leave the variable
unset still exercise the HTTP client with a mock and do not reserve.
