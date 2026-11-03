# -*- coding: utf-8 -*-
"""Public-domain and free-license image sourcing.

Producers call this before a Flux / Gemini still so a real photograph can
stand in for a generated one. The file that comes back is a template input.
Callers composite a card, hook, or reel treatment and do not publish it raw.
"""

from production.providers.image.free.attribution import append_credit, credit_line
from production.providers.image.free.integrate import (
    load_card_source,
    wrap_image_generate,
)
from production.providers.image.free.policy import resolve_policy
from production.providers.image.free.sourcer import resolve_image_source

__all__ = [
    "append_credit",
    "credit_line",
    "load_card_source",
    "resolve_image_source",
    "resolve_policy",
    "wrap_image_generate",
]
