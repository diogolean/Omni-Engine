# -*- coding: utf-8 -*-
"""Ground each reel still in the episode's physical subject.

The image prompt is built here, before Flux, so a beat about an artefact
does not become a city or a ruin. Past QA outcomes live in a small JSONL
ledger under the channel review folder. The same file stores the
narrative angle used for an Ancient Knowledge topic. Super Memory is not
used: that store is the aiwake debate daemon and is not available on the
ARM image.
"""
from production.formats.economic_reel.grounding.prompt import ground_image_prompt

__all__ = ["ground_image_prompt"]
