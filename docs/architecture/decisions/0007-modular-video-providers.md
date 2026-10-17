# ADR 0007 — Modular video providers

## Status

Accepted

## Context

Channels sometimes need a generated clip instead of a still. The first consumer is Endless Summer Paradise. The owner is on an ElevenLabs Pro plan and already uses `ELEVENLABS_API_KEY` for speech. ElevenLabs Flows can render Veo 3.1 video on that key. The Oracle host has no public port, so a workspace webhook cannot be the completion signal.

Gemini Veo and Kling (official API or fal.ai) are later options. They should not grow a second call path.

## Decision

`modules/video_providers` is the only video-generation API in the factory.

1. `VideoProvider` exposes `capabilities`, `submit`, `poll`, `download`, and `estimate_cost`. `VideoRequest` carries prompt, negative prompt, duration, aspect, resolution, audio, start frame, end frame, reference images, and seed. `capabilities(request)` rejects a bad request before any call that spends credits.
2. The active backend is ElevenLabs Flows. The default model is `veo-3.1-fast-generate-001`. The quality model is `veo-3.1-generate-001`. Veo accepts 4, 6, or 8 seconds, `16:9` or `9:16`, and `720p`, `1080p`, or `4K`. Seedance model ids are sent only when `VIDEO_ALLOW_SEEDANCE` (or the channel prefix, such as `ESP_VIDEO_ALLOW_SEEDANCE`) is true, because the API returns `model_access_denied` until ElevenLabs approves access.
3. Completion is polled. The gap is at least 10 seconds and grows (10, 15, 22.5, … capped at 60) until `VIDEO_POLL_TIMEOUT_S` (default 900). `failure_reason` maps to retryable (`timeout`, `model_error`, `dependency_failed`, `internal_error`) or not (`moderated`, `invalid_parameters`, `charging_failed`, and any unknown reason). Failed generations are not charged.
4. The signed `content_url` is saved to a sibling `.part` file and published with `os.replace`. An existing mp4, sidecar, or `.part` file is left untouched. The API key is not sent to the download host. HTTP uses the same browser User-Agent as `models_avatar` (`Mozilla/5.0 … Chrome/131`). The installed SDK 2.73.0 sends `elevenlabs/2.73.0`, so this layer does not call `elevenlabs.flows.video` at runtime. The JSON body matches `VideoGenerationRequest_Veo31FastGenerate001` / `VideoGenerationRequest_Veo31Generate001` from that SDK, checked against the create and get docs on 2026-10-17.
5. Each clip gets a `<name>.video.json` sidecar: provider, model, params, job id, `character_count` before and after from `GET /v1/user/subscription`, the delta, and elapsed seconds. That file is the cost table. ElevenLabs does not publish a per-second video price.
6. Gemini Veo and Kling/fal implement the same interface and raise before any network call. Their `estimate_cost` figures are research notes, not a live price card.
7. Endless Summer Paradise configures the switch in `channels_config/endless_summer_paradise/video_config.py`. It ships **off**. `ESP_VIDEO_*` overrides the file and wins over generic `VIDEO_*`.

One explicit test clip (this does not flip the channel switch):

```text
python -m modules.video_providers.cli --provider elevenlabs --model veo-3.1-fast-generate-001 --duration 4 --aspect 9:16 --resolution 720p --prompt "..."
```

The mp4 is `{OUTPUT_PATH}/endless_summer_paradise/clips/<timestamp>-<slug>.mp4`.

## Hosted MCP (not used by the factory)

ElevenLabs hosts an MCP server at `https://api.elevenlabs.io/v1/mcp` (regional hosts use the residency API host with the same `/v1/mcp` path). It is an interactive OAuth connector for assistants such as Cursor. Official docs describe it as workspace agent management; the product page also advertises creative generation. This repository does not register that server, does not send the factory API key to it, and does not call it from a channel. A person can add the URL in their own Cursor MCP settings and sign in with OAuth.

## Consequences

- A channel spends video credits only after its flag is on, or when someone runs the CLI.
- The sidecar is the source for a real price, because the vendor table does not exist.
- CI tests mock HTTP. They do not call ElevenLabs.
- Adding a backend means a new `VideoProvider`, not a new call site in the channel.
