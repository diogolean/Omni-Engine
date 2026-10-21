# -*- coding: utf-8 -*-
"""Civitai REST client for model and LoRA downloads.

Reads ``CIVITAI_API_TOKEN`` from the environment. The token is never logged
and never written into the repository. Tests inject a transport and a token.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from production.providers.avatar.avatar_config import AvatarConfig
from production.providers.avatar.civitai import API_DOWNLOAD, API_ROOT, comfy_subdir, safe_filename
from production.providers.avatar.env import civitai_token
from production.providers.avatar.errors import CivitaiError, HttpStatusError, RetryableCivitaiError, RetryableGenerationError
from production.providers.avatar.http_client import Transport, request
from production.providers.avatar.paths import comfy_models_root

logger = logging.getLogger(__name__)


class CivitaiClient:
    def __init__(
        self,
        token: str | None = None,
        *,
        transport: Transport | None = None,
        models_root: Path | None = None,
        timeout_s: float = 300.0,
    ) -> None:
        self.token = civitai_token() if token is None else token.strip()
        self.transport = transport
        self.models_root = models_root or comfy_models_root()
        self.timeout_s = timeout_s

    def get_model(self, model_id: int) -> dict[str, Any]:
        payload = self._json(f"{API_ROOT}/models/{int(model_id)}")
        if not isinstance(payload, dict):
            raise CivitaiError(f"Unexpected model payload for {model_id}: {payload!r}")
        return payload

    def get_model_version(self, version_id: int) -> dict[str, Any]:
        payload = self._json(f"{API_ROOT}/model-versions/{int(version_id)}")
        if not isinstance(payload, dict):
            raise CivitaiError(f"Unexpected version payload for {version_id}: {payload!r}")
        return payload

    def download_version(self, version_id: int, *, dest_dir: Path | None = None) -> Path:
        """Download one model version into the ComfyUI folder for its type."""
        meta = self.get_model_version(version_id)
        folder = comfy_subdir(meta)
        primary = primary_file(meta)
        filename = safe_filename(str(primary.get("name") or f"{version_id}.safetensors"))
        target_dir = dest_dir or (self.models_root / folder)
        target_dir.mkdir(parents=True, exist_ok=True)
        dest = target_dir / filename
        url = f"{API_DOWNLOAD}/{int(version_id)}"
        response = self._bytes(url)
        disposition = response.header("content-disposition")
        if disposition:
            header_name = _filename_from_disposition(disposition)
            if header_name:
                dest = target_dir / safe_filename(header_name)
        if not response.body:
            raise CivitaiError(f"Empty download for Civitai version {version_id}.")
        dest.write_bytes(response.body)
        logger.info(
            "Civitai download | version=%s type_dir=%s file=%s bytes=%d",
            version_id,
            folder,
            dest.name,
            dest.stat().st_size,
        )
        return dest

    def download_avatar_assets(self, avatar: AvatarConfig) -> list[Path]:
        """Download every avatar asset that has a numeric Civitai version id.

        Files without an id are skipped and logged. The reference archive for
        Sophie includes a version id for the skin-realism LoRA only.
        """
        saved: list[Path] = []
        skipped: list[str] = []
        if avatar.checkpoint.civitai_version_id:
            saved.append(self.download_version(avatar.checkpoint.civitai_version_id))
        else:
            skipped.append(avatar.checkpoint.filename)
        for lora in avatar.all_loras():
            if lora.civitai_version_id:
                saved.append(self.download_version(lora.civitai_version_id))
            else:
                skipped.append(lora.filename)
        if skipped:
            logger.info(
                "Civitai skip | avatar=%s missing version id: %s",
                avatar.id,
                ", ".join(skipped),
            )
        return saved

    def _headers(self) -> dict[str, str]:
        if not self.token:
            raise CivitaiError(
                "CIVITAI_API_TOKEN is not set. Add it to the factory .env "
                "(see .env.example). Do not commit the token."
            )
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }

    def _json(self, url: str) -> Any:
        response = self._bytes(url)
        try:
            return response.json()
        except Exception as exc:  # noqa: BLE001
            raise CivitaiError(f"Non-JSON Civitai response from {url}") from exc

    def _bytes(self, url: str):
        try:
            return request(
                "GET",
                url,
                headers=self._headers(),
                timeout=self.timeout_s,
                transport=self.transport,
                error_cls=RetryableCivitaiError,
            )
        except HttpStatusError as exc:
            if exc.retryable:
                raise RetryableCivitaiError(str(exc)) from exc
            raise CivitaiError(str(exc)) from exc
        except RetryableGenerationError as exc:
            raise RetryableCivitaiError(str(exc)) from exc


def primary_file(version: dict[str, Any]) -> dict[str, Any]:
    files = version.get("files") or []
    if not isinstance(files, list) or not files:
        raise CivitaiError(f"Civitai version {version.get('id')} has no files.")
    safetensors = [
        item for item in files
        if isinstance(item, dict) and str(item.get("name") or "").lower().endswith(".safetensors")
    ]
    pool = safetensors or [item for item in files if isinstance(item, dict)]
    primary = [
        item for item in pool
        if item.get("primary") is True or str(item.get("type") or "").lower() == "model"
    ]
    return (primary or pool)[0]


def _filename_from_disposition(header: str) -> str | None:
    match = re.search(r"filename\*?=(?:UTF-8''|\"?)([^\";]+)", header, re.IGNORECASE)
    if not match:
        return None
    return match.group(1).strip().strip('"')
