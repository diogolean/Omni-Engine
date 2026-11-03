# -*- coding: utf-8 -*-
"""Fail CI when a non-test module builds a Backblaze client on its own.

``shared/storage/b2_client.py`` is the only place that may construct a boto3
S3 client for B2 or call b2sdk. ``agents/media/b2_client.py`` is the shim.
RunPod's S3 endpoint is a different service and stays allowed.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "outputs",
    "node_modules",
    ".cursor",
}
_B2_MARKERS = (
    "backblazeb2",
    "B2_KEY_ID",
    "B2_APPLICATION_KEY",
    "B2_ENDPOINT",
)
_B2SDK_CALLS = {"B2Api", "InMemoryAccountInfo", "SqliteAccountInfo"}


def _is_test_path(path: Path) -> bool:
    if "tests" in path.parts:
        return True
    name = path.name
    return name.startswith("test_") or name.endswith("_test.py")


def _under_media_client(path: Path) -> bool:
    """The budgeted B2 client, at its old shim path or its shared.storage home."""
    try:
        relative = path.relative_to(_REPO)
    except ValueError:
        relative = path
    posix = relative.as_posix()
    return posix in {
        "agents/media/b2_client.py",
        "shared/storage/b2_client.py",
    } or relative.parts[:2] == ("agents", "media")


def _is_s3_factory(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Attribute):
        if func.attr not in {"client", "resource"}:
            return False
    elif isinstance(func, ast.Name):
        if func.id not in {"client", "resource"}:
            return False
    else:
        return False
    if node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == "s3":
        return True
    for keyword in node.keywords:
        if keyword.arg in {"service_name", "service"} and isinstance(keyword.value, ast.Constant):
            if keyword.value.value == "s3":
                return True
    return False


def _uses_b2sdk(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "b2sdk" or alias.name.startswith("b2sdk."):
                    return True
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "b2sdk" or module.startswith("b2sdk."):
                return True
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in _B2SDK_CALLS:
                return True
            if isinstance(func, ast.Attribute) and func.attr in _B2SDK_CALLS:
                return True
    return False


def _enclosing_function(tree: ast.AST, call: ast.AST) -> ast.AST | None:
    found: tuple[int, ast.AST] | None = None
    end = getattr(call, "end_lineno", None) or call.lineno
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        node_end = node.end_lineno or node.lineno
        if node.lineno <= call.lineno and node_end >= end:
            span = node_end - node.lineno
            if found is None or span < found[0]:
                found = (span, node)
    return found[1] if found else None


def _names_assigned_from_b2(tree: ast.AST, source: str) -> set[str]:
    """Names whose assignment expression mentions a B2 credential or endpoint."""
    found: set[str] = set()
    for node in ast.walk(tree):
        targets: list[ast.AST] = []
        value: ast.AST | None = None
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
            value = node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
            value = node.value
        if value is None:
            continue
        segment = ast.get_source_segment(source, value) or ""
        if not any(marker in segment for marker in _B2_MARKERS):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                found.add(target.id)
    return found


def _call_names(call: ast.Call) -> set[str]:
    return {node.id for node in ast.walk(call) if isinstance(node, ast.Name)}


def _call_is_for_b2(tree: ast.AST, source: str, call: ast.Call, assigned: set[str]) -> bool:
    segment = ast.get_source_segment(source, call) or ""
    if any(marker in segment for marker in _B2_MARKERS):
        return True
    if _call_names(call) & assigned:
        return True
    if any(name.startswith("B2_") for name in _call_names(call)):
        return True
    owner = _enclosing_function(tree, call)
    if owner is None:
        return any(marker in source for marker in _B2_MARKERS)
    owner_source = ast.get_source_segment(source, owner) or ""
    return any(marker in owner_source for marker in _B2_MARKERS)


def _builds_b2_s3_client(tree: ast.AST, source: str) -> bool:
    assigned = _names_assigned_from_b2(tree, source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_s3_factory(node):
            if _call_is_for_b2(tree, source, node, assigned):
                return True
    return False


def violations_in_source(source: str, *, label: str = "<snippet>") -> list[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        if "boto3" in source or "b2sdk" in source:
            return [f"{label}: syntax error near a B2 client ({exc.msg})"]
        return []
    found: list[str] = []
    if _uses_b2sdk(tree):
        found.append(f"{label}: imports or calls b2sdk")
    if _builds_b2_s3_client(tree, source):
        found.append(f"{label}: constructs a boto3 S3 client for Backblaze B2")
    return found


def _python_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*.py"):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if _is_test_path(path) or _under_media_client(path):
            continue
        files.append(path)
    return files


def find_violations(root: Path) -> list[str]:
    found: list[str] = []
    for path in _python_files(root):
        source = path.read_text(encoding="utf-8", errors="replace")
        relative = path.relative_to(root).as_posix()
        found.extend(violations_in_source(source, label=relative))
    return found


def test_detector_flags_a_direct_b2_client() -> None:
    source = (
        "import boto3\n"
        "def make():\n"
        "    return boto3.resource(\n"
        "        's3', endpoint_url='https://s3.us-east-005.backblazeb2.com'\n"
        "    )\n"
    )
    assert violations_in_source(source)


def test_detector_flags_b2sdk() -> None:
    source = "from b2sdk.v2 import B2Api\napi = B2Api()\n"
    assert violations_in_source(source)


def test_detector_allows_runpod_s3() -> None:
    source = (
        "import boto3\n"
        "def make():\n"
        "    return boto3.client('s3', endpoint_url='https://s3api-eu-ro-1.runpod.io')\n"
    )
    assert violations_in_source(source) == []


def test_detector_flags_an_endpoint_taken_from_b2_env() -> None:
    source = (
        "import os\n"
        "import boto3\n"
        "endpoint = os.getenv('B2_ENDPOINT_URL')\n"
        "def make():\n"
        "    return boto3.client('s3', endpoint_url=endpoint)\n"
    )
    assert violations_in_source(source)


def test_detector_allows_a_b2_refusal_next_to_runpod() -> None:
    source = (
        "import boto3\n"
        "def _refuse(endpoint):\n"
        "    if 'backblazeb2' in endpoint:\n"
        "        raise RuntimeError('use agents.media.b2_client')\n"
        "def make(creds):\n"
        "    _refuse(creds['endpoint'])\n"
        "    return boto3.client('s3', endpoint_url=creds['endpoint'])\n"
    )
    assert violations_in_source(source) == []


def test_detector_allows_a_b2_url_with_no_client() -> None:
    source = "URL = 'https://bucket.s3.us-east-005.backblazeb2.com/clip.mp4'\n"
    assert violations_in_source(source) == []


def test_repo_has_no_direct_b2_clients() -> None:
    found = find_violations(_REPO)
    assert found == [], "Direct B2 clients outside agents/media:\n" + "\n".join(found)


@pytest.mark.parametrize(
    "snippet",
    [
        "import b2sdk\n",
        "import b2sdk.v2 as b2\n",
        "info = InMemoryAccountInfo()\n",
    ],
)
def test_detector_flags_b2sdk_shapes(snippet: str) -> None:
    assert violations_in_source(snippet)
