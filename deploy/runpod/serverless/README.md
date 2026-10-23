# Serverless execution

RunPod serverless and dedicated-pod ComfyUI share one adapter:
`production/providers/image/remote_gpu_manager.py` (`core/remote_gpu_manager.py` is a shim). Workflow JSON lives in `deploy/runpod/workflows/`. `infra/runpod` is a symlink to this tree until M14.

Mode is selected by `REMOTE_GPU_MODE` / `--model-api-flow` (`remote_gpu_pod` vs
`remote_gpu_serverless`). This folder is a placeholder so that split stays
visible without duplicating the manager module.
