"""Model API integration — flow presets and swappable text models."""
from __future__ import annotations

from production.providers.llm.text_model import TextResult, complete_script

__all__ = ["TextResult", "complete_script"]
