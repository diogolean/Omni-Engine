# -*- coding: utf-8 -*-
"""Generation backend interface.

Concrete backends live beside this module. A backend receives an already
built ComfyUI workflow and writes one image to ``job.dest``. It does not
decide prompts or output roots.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class GenerationJob:
    avatar_id: str
    preset: str
    seed: int
    positive: str
    negative: str
    workflow: dict[str, Any]
    dest: Path
    save_node_id: str


@dataclass(frozen=True)
class GenerationResult:
    path: Path
    job_id: str
    backend: str
    seed: int
    preset: str
    prompt: str
    negative: str


class GenerationBackend(Protocol):
    name: str

    def generate(self, job: GenerationJob) -> GenerationResult:
        """Run one workflow and write ``job.dest``."""
