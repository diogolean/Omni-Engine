# -*- coding: utf-8 -*-
"""Every third-party import is pinned, or named as an optional extra.

``requirements.txt`` is the local install. ``requirements-docker.txt`` is the
arm64 image and omits the PC-only publishers and the Aiwake mouth sprites.
A lazy import that the code skips when the package is missing stays in the
allowlist and in the comment block at the bottom of ``requirements.txt``.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "outputs", "node_modules", ".cursor"}

# Import top-level name -> distribution. The test accepts any name in the set.
_DIST: dict[str, frozenset[str]] = {
    "PIL": frozenset({"Pillow"}),
    "cv2": frozenset({"opencv-python-headless", "opencv-python"}),
    "yaml": frozenset({"PyYAML"}),
    "dotenv": frozenset({"python-dotenv"}),
    "sklearn": frozenset({"scikit-learn"}),
    "skia": frozenset({"skia-python"}),
    "imageio_ffmpeg": frozenset({"imageio-ffmpeg"}),
    "botocore": frozenset({"boto3"}),
    "edge_tts": frozenset({"edge-tts"}),
    "google.genai": frozenset({"google-genai"}),
    "google.auth": frozenset({"google-auth"}),
    "google.oauth2": frozenset({"google-auth"}),
    "googleapiclient": frozenset({"google-api-python-client"}),
    "google_auth_oauthlib": frozenset({"google-auth-oauthlib"}),
    "google_auth_httplib2": frozenset({"google-auth-httplib2"}),
}

# Old in-repo names. They are not PyPI distributions.
_INTERNAL = frozenset(
    {
        "app_config",
        "avatar_engine",
        "core_engine",
        "postplanner_exporter",
    }
)

# Imported, and the call site already continues when the import fails.
_OPTIONAL = frozenset(
    {
        "chromadb",
        "open_clip",
        "torch",
        "transformers",
        "pandas",
        "scipy",
        "soundfile",
        "mutagen",
        "imageio",
        "httpx",
        "winocr",
    }
)

# Present in requirements.txt, absent from the arm64 image.
_LOCAL_ONLY = frozenset({"mediapipe", "pyautogui", "playwright", "skia-python"})

_VM_ROOTS = frozenset(
    {
        "production",
        "distribution",
        "shared",
        "engine",
        "animation",
        "accounts",
        "core",
        "modules",
    }
)
_VM_FILES = frozenset({"main.py", "config.py", "channel_loader.py", "layer_wiring.py"})
_VM_SKIP = "distribution/publishers/browser/"


def _requirement_names(path: Path) -> set[str]:
    names: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        name = line.split("[", 1)[0]
        for sep in (">=", "==", "<=", "~=", "!=", ">", "<"):
            name = name.split(sep, 1)[0]
        name = name.strip().lower().replace("_", "-")
        if name:
            names.add(name)
    return names


def _local_modules(root: Path) -> set[str]:
    """Repo modules that are imported as a bare top-level name."""
    names: set[str] = set()
    for path in root.iterdir():
        if path.name.startswith(".") or path.name in _SKIP_DIRS:
            continue
        if path.is_dir() or path.suffix == ".py":
            names.add(path.stem if path.suffix == ".py" else path.name)
    for path in root.rglob("*"):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if path.is_dir() and (path / "__init__.py").is_file():
            names.add(path.name)
        elif path.suffix == ".py":
            names.add(path.stem)
    names.update(_INTERNAL)
    forced = {key.split(".", 1)[0] for key in _DIST}
    forced.update(_OPTIONAL)
    forced.update({"google", "playwright", "numpy", "pytest", "requests", "rich"})
    return names - forced


def _dists(module: str, names: tuple[str, ...]) -> frozenset[str]:
    if module == "google" and "genai" in names:
        return frozenset({"google-genai"})
    for prefix, dists in _DIST.items():
        if module == prefix or module.startswith(prefix + "."):
            return dists
    top = module.split(".", 1)[0]
    return frozenset({top.replace("_", "-")})


def _is_type_checking(test: ast.expr) -> bool:
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return True
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


class _Imports(ast.NodeVisitor):
    def __init__(self) -> None:
        self.found: list[tuple[str, tuple[str, ...], int]] = []

    def visit_If(self, node: ast.If) -> None:
        if _is_type_checking(node.test):
            for stmt in node.orelse:
                self.visit(stmt)
            return
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.found.append((alias.name, (), node.lineno))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level or not node.module:
            return
        names = tuple(alias.name for alias in node.names)
        self.found.append((node.module, names, node.lineno))


def _python_files(root: Path) -> list[Path]:
    found: list[Path] = []
    for path in root.rglob("*.py"):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        found.append(path)
    return found


def _third_party(root: Path) -> dict[str, list[str]]:
    """Map an import module to ``relative:line`` sites. Stdlib and local code are out."""
    local = _local_modules(root)
    stdlib = set(sys.stdlib_module_names)
    sites: dict[str, list[str]] = {}
    for path in _python_files(root):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        visitor = _Imports()
        visitor.visit(tree)
        rel = path.relative_to(root).as_posix()
        for module, names, lineno in visitor.found:
            top = module.split(".", 1)[0]
            if top in stdlib or top in local:
                continue
            sites.setdefault(module, []).append(f"{rel}:{lineno}")
    return sites


def _satisfied(module: str, names: tuple[str, ...], reqs: set[str]) -> bool:
    top = module.split(".", 1)[0]
    if top in _OPTIONAL or module in _OPTIONAL:
        return True
    dists = {item.lower() for item in _dists(module, names)}
    return bool(dists & reqs)


def _import_names(module: str) -> tuple[str, ...]:
    if module == "google":
        return ("genai",)
    return ()


def _on_vm(site: str) -> bool:
    path = site.split(":", 1)[0]
    if path.startswith(_VM_SKIP):
        return False
    if path in _VM_FILES:
        return True
    return path.split("/", 1)[0] in _VM_ROOTS


def test_third_party_imports_are_in_requirements() -> None:
    reqs = _requirement_names(_REPO / "requirements.txt")
    ci = _requirement_names(_REPO / "requirements-ci.txt")
    missing: list[str] = []
    for module, sites in sorted(_third_party(_REPO).items()):
        if _satisfied(module, _import_names(module), reqs):
            continue
        production = [site for site in sites if "/tests/" not in f"/{site.split(':', 1)[0]}"]
        if not production and _satisfied(module, _import_names(module), ci):
            continue
        shown = production or sites
        missing.append(f"{module} -> {sorted(_dists(module, _import_names(module)))}: {shown[:4]}")
    assert not missing, "third-party imports missing from requirements.txt:\n" + "\n".join(missing)


def test_vm_imports_are_in_the_docker_requirements() -> None:
    docker = _requirement_names(_REPO / "requirements-docker.txt")
    missing: list[str] = []
    for module, sites in sorted(_third_party(_REPO).items()):
        vm_sites = [site for site in sites if _on_vm(site)]
        if not vm_sites:
            continue
        if _satisfied(module, _import_names(module), docker):
            continue
        dists = {item.lower() for item in _dists(module, _import_names(module))}
        if dists & {name.lower() for name in _LOCAL_ONLY}:
            continue
        if module.split(".", 1)[0] in _OPTIONAL:
            continue
        missing.append(f"{module}: {vm_sites[:4]}")
    assert not missing, "VM imports missing from requirements-docker.txt:\n" + "\n".join(missing)


def test_playwright_and_skia_stay_off_the_arm64_image() -> None:
    local = _requirement_names(_REPO / "requirements.txt")
    docker = _requirement_names(_REPO / "requirements-docker.txt")
    text = (_REPO / "requirements.txt").read_text(encoding="utf-8")
    assert "playwright>=1.64,<2" in text
    assert "playwright" in local
    assert "skia-python" in local
    assert "playwright" not in docker
    assert "skia-python" not in docker
    assert "mediapipe" not in docker
    assert "pyautogui" not in docker


def test_optional_extras_are_named_in_requirements() -> None:
    text = (_REPO / "requirements.txt").read_text(encoding="utf-8")
    unnamed = [name for name in sorted(_OPTIONAL) if name not in text]
    assert unnamed == []
    assert "botocore" in text


def test_a_missing_import_fails_the_check() -> None:
    """An import with no pin is not treated as satisfied."""
    assert _satisfied("playwright", (), _requirement_names(_REPO / "requirements.txt"))
    assert not _satisfied("playwright", (), set())
    assert not _satisfied("not_a_real_distribution", (), _requirement_names(_REPO / "requirements.txt"))