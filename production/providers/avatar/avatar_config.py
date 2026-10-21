# -*- coding: utf-8 -*-
"""Load one self-contained avatar folder.

An avatar is ``models_avatar/avatars/<id>/avatar.yaml`` plus an API-format
ComfyUI workflow and a prompt library. Adding a character is a new folder,
not a change to ``channel_loader``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from production.providers.avatar.errors import AvatarConfigError

_PACKAGE_ROOT = Path(__file__).resolve().parent
AVATARS_ROOT = _PACKAGE_ROOT / "avatars"

_REQUIRED_NODES = (
    "checkpoint",
    "identity_lora",
    "positive",
    "negative",
    "latent",
    "sampler",
    "decode",
    "save",
)


@dataclass(frozen=True)
class Identity:
    name: str
    trigger: str


@dataclass(frozen=True)
class CheckpointSpec:
    filename: str
    civitai_model_id: int | None = None
    civitai_version_id: int | None = None


@dataclass(frozen=True)
class LoraSpec:
    filename: str
    weight: float
    clip_weight: float | None = None
    civitai_model_id: int | None = None
    civitai_version_id: int | None = None
    node: str | None = None
    civitai_file_token: str | None = None

    @property
    def clip(self) -> float:
        return self.weight if self.clip_weight is None else self.clip_weight


@dataclass(frozen=True)
class DetailerSpec:
    enabled: bool
    detector: str = ""
    denoise: float = 0.0
    steps: int = 20
    cfg: float = 5.0
    identity_lora_weight: float = 0.85


@dataclass(frozen=True)
class PresetSpec:
    name: str
    sampler_name: str
    scheduler: str
    steps: int
    cfg: float
    width: int
    height: int
    denoise: float = 1.0
    face_detailer: DetailerSpec = field(default_factory=lambda: DetailerSpec(False))
    hand_detailer: DetailerSpec = field(default_factory=lambda: DetailerSpec(False))


@dataclass(frozen=True)
class GraphSpec:
    workflow_file: str
    nodes: dict[str, str]


@dataclass(frozen=True)
class AvatarConfig:
    id: str
    directory: Path
    identity: Identity
    checkpoint: CheckpointSpec
    identity_lora: LoraSpec
    extra_loras: tuple[LoraSpec, ...]
    negatives: str
    quality_suffix: str
    extra_forbid_tokens: tuple[str, ...]
    graph: GraphSpec
    presets: dict[str, PresetSpec]
    default_preset: str = "fast"

    def preset(self, name: str | None = None) -> PresetSpec:
        key = (name or self.default_preset).strip()
        try:
            return self.presets[key]
        except KeyError as exc:
            known = ", ".join(sorted(self.presets)) or "(none)"
            raise AvatarConfigError(
                f"Avatar {self.id!r} has no preset {key!r}. Known: {known}."
            ) from exc

    @property
    def workflow_path(self) -> Path:
        return self.directory / self.graph.workflow_file

    def all_loras(self) -> tuple[LoraSpec, ...]:
        return (self.identity_lora, *self.extra_loras)

    def downloadable_versions(self) -> list[tuple[str, int]]:
        """``(role, civitai version id)`` for assets that have a numeric id."""
        found: list[tuple[str, int]] = []
        if self.checkpoint.civitai_version_id:
            found.append(("checkpoint", self.checkpoint.civitai_version_id))
        for lora in self.all_loras():
            if lora.civitai_version_id:
                found.append((lora.filename, lora.civitai_version_id))
        return found


def avatars_root() -> Path:
    return AVATARS_ROOT


def list_avatar_ids() -> list[str]:
    root = avatars_root()
    if not root.is_dir():
        return []
    return sorted(
        path.parent.name
        for path in root.glob("*/avatar.yaml")
        if path.is_file()
    )


def avatar_dir(avatar_id: str) -> Path:
    slug = (avatar_id or "").strip().lower()
    path = avatars_root() / slug
    if not (path / "avatar.yaml").is_file():
        known = ", ".join(list_avatar_ids()) or "(none)"
        raise AvatarConfigError(
            f"Unknown avatar {avatar_id!r}. Expected {path / 'avatar.yaml'}. "
            f"Known: {known}."
        )
    return path


def load_avatar(avatar_id: str) -> AvatarConfig:
    directory = avatar_dir(avatar_id)
    raw = _read_yaml(directory / "avatar.yaml")
    if not isinstance(raw, dict):
        raise AvatarConfigError(f"{directory / 'avatar.yaml'} must be a mapping.")
    return _parse_avatar(directory, raw)


def _read_yaml(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise AvatarConfigError(f"Cannot read {path}: {exc}") from exc
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise AvatarConfigError(f"Invalid YAML in {path}: {exc}") from exc


def _parse_avatar(directory: Path, raw: dict[str, Any]) -> AvatarConfig:
    slug = str(raw.get("id") or directory.name).strip().lower()
    if slug != directory.name:
        raise AvatarConfigError(
            f"Avatar id {slug!r} does not match folder {directory.name!r}."
        )
    identity_raw = _mapping(raw, "identity")
    identity = Identity(
        name=str(identity_raw.get("name") or "").strip(),
        trigger=str(identity_raw.get("trigger") or "").strip(),
    )
    if not identity.name or not identity.trigger:
        raise AvatarConfigError(f"Avatar {slug} needs identity.name and identity.trigger.")

    graph_raw = _mapping(raw, "graph")
    nodes_raw = graph_raw.get("nodes") or {}
    if not isinstance(nodes_raw, dict):
        raise AvatarConfigError(f"Avatar {slug} graph.nodes must be a mapping.")
    nodes = {str(key): str(value) for key, value in nodes_raw.items()}
    missing = [key for key in _REQUIRED_NODES if not nodes.get(key)]
    if missing:
        raise AvatarConfigError(
            f"Avatar {slug} graph.nodes is missing: {', '.join(missing)}."
        )
    workflow_file = str(graph_raw.get("workflow") or "").strip()
    if not workflow_file:
        raise AvatarConfigError(f"Avatar {slug} graph.workflow is required.")
    workflow_path = directory / workflow_file
    if not workflow_path.is_file():
        raise AvatarConfigError(f"Workflow file not found: {workflow_path}")

    extra_raw = raw.get("extra_loras") or []
    if not isinstance(extra_raw, list):
        raise AvatarConfigError(f"Avatar {slug} extra_loras must be a list.")

    policy = raw.get("prompt_policy") or {}
    if not isinstance(policy, dict):
        raise AvatarConfigError(f"Avatar {slug} prompt_policy must be a mapping.")
    extra_forbid = tuple(
        str(item).strip()
        for item in (policy.get("extra_forbid_tokens") or [])
        if str(item).strip()
    )

    presets_raw = raw.get("presets") or {}
    if not isinstance(presets_raw, dict) or not presets_raw:
        raise AvatarConfigError(f"Avatar {slug} needs at least one preset.")
    presets = {
        str(name): _parse_preset(str(name), value)
        for name, value in presets_raw.items()
    }
    default_preset = str(raw.get("default_preset") or "fast").strip()
    if default_preset not in presets:
        raise AvatarConfigError(
            f"Avatar {slug} default_preset {default_preset!r} is not defined."
        )

    config = AvatarConfig(
        id=slug,
        directory=directory,
        identity=identity,
        checkpoint=_parse_checkpoint(_mapping(raw, "checkpoint")),
        identity_lora=_parse_lora(_mapping(raw, "identity_lora"), required_node=nodes["identity_lora"]),
        extra_loras=tuple(_parse_lora(item) for item in extra_raw),
        negatives=str(raw.get("negatives") or "").strip(),
        quality_suffix=str(raw.get("quality_suffix") or "").strip(),
        extra_forbid_tokens=extra_forbid,
        graph=GraphSpec(workflow_file=workflow_file, nodes=nodes),
        presets=presets,
        default_preset=default_preset,
    )
    _validate_workflow_nodes(config)
    return config


def _parse_checkpoint(raw: dict[str, Any]) -> CheckpointSpec:
    filename = str(raw.get("filename") or "").strip()
    if not filename:
        raise AvatarConfigError("checkpoint.filename is required.")
    return CheckpointSpec(
        filename=filename,
        civitai_model_id=_opt_int(raw.get("civitai_model_id")),
        civitai_version_id=_opt_int(raw.get("civitai_version_id")),
    )


def _parse_lora(raw: dict[str, Any], *, required_node: str | None = None) -> LoraSpec:
    if not isinstance(raw, dict):
        raise AvatarConfigError("Each LoRA entry must be a mapping.")
    filename = str(raw.get("filename") or "").strip()
    if not filename:
        raise AvatarConfigError("LoRA filename is required.")
    if "weight" not in raw:
        raise AvatarConfigError(f"LoRA {filename} is missing weight.")
    node = str(raw.get("node") or required_node or "").strip() or None
    return LoraSpec(
        filename=filename,
        weight=float(raw["weight"]),
        clip_weight=_opt_float(raw.get("clip_weight")),
        civitai_model_id=_opt_int(raw.get("civitai_model_id")),
        civitai_version_id=_opt_int(raw.get("civitai_version_id")),
        node=node,
        civitai_file_token=_opt_str(raw.get("civitai_file_token")),
    )


def _parse_preset(name: str, raw: Any) -> PresetSpec:
    if not isinstance(raw, dict):
        raise AvatarConfigError(f"Preset {name!r} must be a mapping.")
    try:
        sampler = str(raw["sampler_name"]).strip()
        scheduler = str(raw["scheduler"]).strip()
        steps = int(raw["steps"])
        cfg = float(raw["cfg"])
        width = int(raw["width"])
        height = int(raw["height"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AvatarConfigError(
            f"Preset {name!r} needs sampler_name, scheduler, steps, cfg, width, height."
        ) from exc
    face = _parse_detailer(raw.get("face_detailer"), preset=name, kind="face_detailer")
    hand = _parse_detailer(raw.get("hand_detailer"), preset=name, kind="hand_detailer")
    return PresetSpec(
        name=name,
        sampler_name=sampler,
        scheduler=scheduler,
        steps=steps,
        cfg=cfg,
        width=width,
        height=height,
        denoise=float(raw.get("denoise", 1)),
        face_detailer=face,
        hand_detailer=hand,
    )


def _parse_detailer(raw: Any, *, preset: str, kind: str) -> DetailerSpec:
    if raw is None:
        return DetailerSpec(enabled=False)
    if not isinstance(raw, dict):
        raise AvatarConfigError(f"Preset {preset!r} {kind} must be a mapping.")
    enabled = bool(raw.get("enabled", False))
    detector = str(raw.get("detector") or "").strip()
    if enabled and not detector:
        raise AvatarConfigError(f"Preset {preset!r} {kind} is enabled but has no detector.")
    return DetailerSpec(
        enabled=enabled,
        detector=detector,
        denoise=float(raw.get("denoise") or 0),
        steps=int(raw.get("steps") or 20),
        cfg=float(raw.get("cfg") or 5),
        identity_lora_weight=float(raw.get("identity_lora_weight") or 0.85),
    )


def _validate_workflow_nodes(config: AvatarConfig) -> None:
    try:
        text = config.workflow_path.read_text(encoding="utf-8-sig")
        workflow = yaml.safe_load(text) if config.workflow_path.suffix in {".yaml", ".yml"} else None
    except OSError as exc:
        raise AvatarConfigError(f"Cannot read {config.workflow_path}: {exc}") from exc
    if workflow is None:
        import json

        try:
            workflow = json.loads(config.workflow_path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            raise AvatarConfigError(f"Invalid workflow JSON: {config.workflow_path}: {exc}") from exc
    if not isinstance(workflow, dict):
        raise AvatarConfigError(f"Workflow {config.workflow_path} must be an API prompt object.")
    for key, node_id in config.graph.nodes.items():
        if node_id not in workflow:
            raise AvatarConfigError(
                f"Avatar {config.id} graph.nodes.{key} points at missing node {node_id}."
            )
    for lora in config.extra_loras:
        if lora.node and lora.node not in workflow:
            raise AvatarConfigError(
                f"Avatar {config.id} extra LoRA {lora.filename} points at missing node {lora.node}."
            )
        if not lora.node:
            raise AvatarConfigError(
                f"Avatar {config.id} extra LoRA {lora.filename} needs a workflow node id."
            )


def _mapping(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key)
    if not isinstance(value, dict):
        raise AvatarConfigError(f"avatar.yaml key {key!r} must be a mapping.")
    return value


def _opt_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _opt_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
