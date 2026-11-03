# models_avatar

Still-image personas for OmniEngine. Each avatar is a folder. The shared code loads that folder, builds a ComfyUI prompt, and writes the image under the factory `OUTPUT_PATH`.

This is not a channel. `channel_loader.VALID_PAGES` is unchanged. Video pages (voice, DNA, posting) and avatar stills do not share a config format. `channel_bridge.channel_descriptor` exposes the fields a later registration would need, and it reports `registered_in_channel_loader: false`.

## Layout

```
models_avatar/
  avatars/<avatar_id>/
    avatar.yaml            # identity, checkpoint, LoRAs, presets
    workflow_fast.json     # ComfyUI API prompt for the single pass
    prompts/templates.yaml
    prompts/PROMPT_GUIDE.md
  backends/                # runpod_pod (default), runpod_serverless, stubs
  civitai/                 # download client + training scaffold
```

Generated files:

```
{OUTPUT_PATH}/models_avatar/<avatar_id>/<YYYY-MM-DD>/<preset>/<batch_id>/
  0000_<seed>.png
  plan.json
  manifest.jsonl
```

`OUTPUT_PATH` is the existing factory variable (the Google Drive root in `.env`). The helper is `utils.pipeline_paths.outputs_path`. There is no second media root. Logs go to `outputs/logs/models_avatar.log` through `pipeline_logs_dir()`.

Model downloads (not generated media) go to `MODELS_AVATAR_MODELS_DIR` when that is set, otherwise `{OUTPUT_PATH}/models_avatar/_models/<comfy subfolder>/`. Point the override at a pod `ComfyUI/models` directory when you want the files on the GPU machine. Nothing in this package writes a `.safetensors` into the git tree.

## Commands

```bash
python -m production.providers.avatar list
python -m production.providers.avatar generate --avatar sophie_bennet --preset fast --set lifestyle --template-id cafe_morning
python -m production.providers.avatar batch --avatar sophie_bennet --set lifestyle --count 200 --preset fast --batch-id night-1
python -m production.providers.avatar download-models --avatar sophie_bennet
```

`batch` is resumable. The same `--batch-id` skips manifest rows with `"status": "ok"`. Transport errors retry (`--max-attempts`, default 3). `--interval` is the pause between jobs (`MODELS_AVATAR_MIN_INTERVAL_S` when omitted). The manifest records `prompt`, `negative`, `seed`, `preset`, and `file` for every image.

A single `generate` writes `{timestamp}_{id}.png` and a sidecar `{timestamp}_{id}.json` with `prompt`, `negative`, `seed`, `preset`, and `workflow`. The name is not the seed, and an existing file is not overwritten. The pod publishes the image by writing `name.png.part` and `os.replace`, so Drive sync does not upload a partial file.

On `ultra_detail`, the hand detailer reuses the face LoRA node. The weight in effect is the face `identity_lora_weight` (0.85 for Sophie). The hand weight is used only when the face detailer is turned off.

## Backends

`MODELS_AVATAR_BACKEND` defaults to `runpod_pod`. That default is separate from `REMOTE_GPU_MODE`, which the video factory leaves on serverless. Avatar jobs use the live pod until the serverless image is rebuilt.

| Backend | Status |
|---|---|
| `runpod_pod` | Default. ComfyUI `POST /prompt`, `GET /history/{id}`, `GET /view`. |
| `runpod_serverless` | Payload and polling implemented. The image still needs the weights below. |
| `civitai_onsite` | Interface only. No image call. |
| `gemini_image` | Interface only. No Gemini call. |

Pod URL, first hit wins: `MODELS_AVATAR_POD_URL`, `REMOTE_GPU_BASE_URL`, `COMFYUI_BASE_URL`. The Cloudflare proxy in front of `*.proxy.runpod.net` blocks Python's default urllib user agent, so every request sends a browser User-Agent. When `RUNPOD_API_KEY` is set it is sent as `Authorization: Bearer`.

Serverless uses `RUNPOD_API_KEY` and `RUNPOD_ENDPOINT_ID` (or `RUNPOD_ENDPOINT_URL`). The body is the same shape the factory worker already accepts:

```json
{
  "input": {"workflow": {"<node id>": {"class_type": "...", "inputs": {}}}},
  "policy": {"executionTimeout": 600000}
}
```

The worker must return image bytes as base64 (`data`, `image`, or `data_base64`) or an `https` URL. This package does not invent a fake image when the worker returns neither.

### Serverless image

`fast` needs ComfyUI plus the checkpoint and LoRAs named in the avatar file.

`ultra_detail` also needs:

- ComfyUI-Impact-Pack (`FaceDetailer`)
- `UltralyticsDetectorProvider`
- the `ultralytics` Python package
- `models/ultralytics/bbox/face_yolov8m.pt`
- `models/ultralytics/bbox/hand_yolov8s.pt`

The detailer graph does not connect a SAM loader, matching the pod script that produced the reference. Checkpoints belong in `models/checkpoints`. LoRAs belong in `models/loras`. `image_requirements(avatar)` in `backends/runpod_serverless.py` builds this list from the avatar file so a new character does not require a doc edit.

## Civitai

`CIVITAI_API_TOKEN` comes from the environment. See `.env.example`. The client downloads `GET /api/v1/model-versions/{id}` metadata and `GET /api/download/models/{id}` into the ComfyUI folder for that model type (LoRA → `loras/`, checkpoint → `checkpoints/`).

`civitai/training.py` builds a training payload and stops. The public API does not document a route that creates a LoRA training job. `submit_training_job` raises `NotImplementedError` and the TODO in that function names the routes that do exist.

## Add an avatar

1. Copy `avatars/sophie_bennet` to `avatars/<slug>`.
2. Set `id` to the folder name. Set `identity.name` and `identity.trigger`.
3. Set the checkpoint filename, the identity LoRA filename and weight, and any extra LoRAs. Put Civitai version ids on the entries you want `download-models` to fetch.
4. Point each LoRA `node` at a `LoraLoader` in `workflow_fast.json`, and keep `graph.nodes` aligned with that file. The fast graph for Sophie is the single pass: checkpoint `cyberrealisticXL_v100_fp16`, identity LoRA 0.75, `perfection_style_SDXL` 0.6, `skin_texture_XL_v4` 0.10, `skin_realism_imperfections_xl` 0.10, `dpmpp_2m` / `karras`, 30 steps, cfg 5, 832×1216. The text baked into that export is replaced on every run. Hair, eye, and age words in the export (the original scene said "blonde") are not sent.
5. Add `prompts/templates.yaml` and a short prompt guide. Outfits name a top and a bottom.
6. Run `python -m pytest tests/models_avatar -q`.

No Python change is required for a new folder. `list_avatar_ids()` reads `avatars/*/avatar.yaml`.

Do not put `.safetensors`, API tokens, or generated images in the folder you commit.

## Integration points

- Output root: `utils.pipeline_paths.outputs_path("models_avatar", ...)`, which reads `OUTPUT_PATH` / `OUTPUTS_DIR`.
- Pod URL and serverless ids: `REMOTE_GPU_BASE_URL`, `COMFYUI_BASE_URL`, `RUNPOD_API_KEY`, `RUNPOD_ENDPOINT_ID`, `RUNPOD_ENDPOINT_URL`.
- Poll and timeout fall back to `REMOTE_GPU_POLL_INTERVAL_S` and `REMOTE_GPU_TIMEOUT_S`.
- Logs: `utils.pipeline_paths.pipeline_logs_dir()`.
- Optional future channel hook: `models_avatar.channel_bridge.channel_descriptor`.
- Safety list (not configurable off): `models_avatar.safety.MANDATORY_NEGATIVE_TERMS`.
