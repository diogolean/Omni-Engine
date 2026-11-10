"""Every provider call in the core sits inside the spend guard."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path("products/animind_core")
CALLS = {
    "httpx.post",
    "edge_tts.Communicate",
    "generate_character_image",
    "chroma_key_cutout",
    "remove_background",
    "birefnet_cutout",
}
GUARDS = {"billable", "admit_unpriced", "note_unbilled"}


def _name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def test_provider_calls_sit_inside_the_guard():
    leaks: list[str] = []
    for path in ROOT.rglob("*.py"):
        if "tests" in path.parts or path.name == "guard.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                leaks.extend(_unguarded(path, node))
            elif isinstance(node, ast.ClassDef):
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        leaks.extend(_unguarded(path, child))
    assert leaks == []


def _unguarded(path: Path, node: ast.AST) -> list[str]:
    calls: list[str] = []
    guarded = False
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        name = _name(child.func)
        short = name.rsplit(".", 1)[-1]
        if name in CALLS or short in CALLS:
            calls.append(name)
        if short in GUARDS:
            guarded = True
    if calls and not guarded:
        return [f"{path}:{getattr(node, 'name', '?')}:{','.join(calls)}"]
    return []
