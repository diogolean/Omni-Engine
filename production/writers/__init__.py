"""Script and caption writers. Model I/O goes through agents.mcp.

Import concrete modules directly (``agents.writer.caption_engine``, etc.)
to avoid a package-level cycle with the orchestrator.
"""
from __future__ import annotations

__all__ = [
    "CaptionEngine",
    "CopywriterAgent",
    "WriterBrief",
    "compose",
    "draft_to_script",
    "generate_script",
    "get_script_llm_call_log",
    "get_script_cost_log",
    "judge_draft",
]


def __getattr__(name: str):
    if name == "WriterBrief":
        from production.writers.writer_brief import WriterBrief

        return WriterBrief
    if name in {"compose", "draft_to_script"}:
        from production.writers import script_brain

        return getattr(script_brain, name)
    if name == "judge_draft":
        from production.qa.judges.judge_gate import judge_draft

        return judge_draft
    if name == "CaptionEngine":
        from production.writers.caption_engine import CaptionEngine

        return CaptionEngine
    if name == "CopywriterAgent":
        from production.writers.copywriter import CopywriterAgent

        return CopywriterAgent
    if name == "generate_script":
        from production.writers.script_agent import generate_script

        return generate_script
    if name == "get_script_llm_call_log":
        from production.writers.script_agent import get_script_llm_call_log

        return get_script_llm_call_log
    if name == "get_script_cost_log":
        from production.writers.script_agent import get_script_cost_log

        return get_script_cost_log
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
