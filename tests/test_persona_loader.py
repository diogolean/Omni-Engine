# -*- coding: utf-8 -*-
"""The engine loader fills one generic prompt from each channel's own DNA file."""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from production.writers.persona import ENGINE_ROOT, channel_profile_rows, load_persona

_REPO = Path(__file__).resolve().parents[1]
_FIXTURES = _REPO / "tests" / "fixtures"


def _engine_view(channel: str) -> dict:
    persona = load_persona(channel)
    row = {
        "display": persona.display_name,
        "age": persona.age_years,
        "physical": persona.physical,
        "voice": persona.voice_principles,
        "lens": persona.expertise_lens,
        "directive": persona.core_directive,
        "context": persona.persona_context_block(),
        "philosophy": persona.philosophy_block(),
        "cta": list(persona.cta_keywords),
    }
    try:
        row["visual"] = persona.visual_style_block()
    except TypeError:
        row["visual_error"] = "TypeError"
    return row


def test_engine_prompts_match_the_channel_dna_bytes() -> None:
    gold = json.loads((_FIXTURES / "engine_persona_prompts.json").read_text(encoding="utf-8"))
    for channel, expected in gold.items():
        assert _engine_view(channel) == expected


def test_channel_folder_prompts_stay_in_that_folder() -> None:
    gold = json.loads((_FIXTURES / "channel_persona_prompts.json").read_text(encoding="utf-8"))
    for channel, expected in gold.items():
        module = __import__(
            f"channels_config.{channel}.persona_dna",
            fromlist=["persona_context_block"],
        )
        for name, value in expected.items():
            if name == "context":
                assert module.persona_context_block() == value
            elif name == "philosophy":
                assert module.philosophy_block() == value
            elif name == "visual":
                assert module.visual_style_block() == value
            elif name == "visual_error":
                with pytest.raises(TypeError):
                    module.visual_style_block()
            else:
                assert getattr(module, name) == value


def test_channel_persona_modules_do_not_import_production() -> None:
    root = _REPO / "channels_config"
    for path in root.glob("*/persona_dna.py"):
        if path.parent.name == "aiwake":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            module = ""
            if isinstance(node, ast.ImportFrom) and node.module:
                module = node.module
            elif isinstance(node, ast.Import):
                module = " ".join(alias.name for alias in node.names)
            assert not module.startswith("production"), path


def test_a_new_channel_is_only_a_folder(tmp_path: Path) -> None:
    channel = tmp_path / "channels_config" / "brand_new_voice"
    channel.mkdir(parents=True)
    (channel / "master_dna.json").write_text(
        json.dumps(
            {
                "Subject": "New Voice",
                "Physical_Appearance": {"Age": 4, "Hair": "h", "Physique": "p", "Skin_Texture": "s", "Visual_Standard": "v"},
                "Brand_Voice_&_Mission": {
                    "Tone": "t",
                    "Mission_Statement": "m",
                    "Themes": "th",
                    "Target_Audience": "a",
                    "Voice_Persona": "vp",
                },
                "Core_Directive": "stay local",
                "Core_Natural_Mechanics": ["one"],
                "Philosophical_Core": {
                    "Core_Belief": "b",
                    "The_Conflict": "c",
                    "Science_Metaphors": ["met"],
                    "Banned_Name_References": [],
                },
                "Narrative_Angles": [{"code": "X", "description": "y"}],
                "Call_To_Action_Inventory": {"Authorized_Keywords": ["GO"]},
                "Environments": [{"name": "Shore"}],
            }
        ),
        encoding="utf-8",
    )
    (channel / "page_config.py").write_text(
        'PAGE_DISPLAY_NAME = "Brand New Voice"\n'
        'DEFAULT_FORMAT = "IMAGE_QUOTE"\n'
        'DEFAULT_AVATAR_MODE = "OFF"\n',
        encoding="utf-8",
    )
    persona = load_persona("brand_new_voice", root=tmp_path)
    assert persona.display_name == "New Voice"
    assert "stay local" in persona.persona_context_block()
    assert "Shore" in persona.visual_style_block()
    source = (_REPO / "production" / "writers" / "persona.py").read_text(encoding="utf-8")
    assert "brand_new_voice" not in source
    rows = channel_profile_rows(tmp_path)
    assert rows == [
        {
            "channel": "brand_new_voice",
            "display_name": "Brand New Voice",
            "default_format": "IMAGE_QUOTE",
            "avatar_mode": "OFF",
            "dna": "channels_config/brand_new_voice/master_dna.json",
        }
    ]


def test_profile_rows_come_from_each_channel_folder() -> None:
    rows = channel_profile_rows()
    names = [row["channel"] for row in rows]
    assert names == sorted(names)
    assert "aiwake" not in names
    assert "anna_protocol" in names
    assert "master_mei" in names
    by_name = {row["channel"]: row for row in rows}
    assert by_name["master_mei"]["display_name"] == "Master Mei | Mind Control"
    assert by_name["ancient_knowledge"]["default_format"] == "IMAGE_BACKGROUND"
    on_disk = []
    for folder in (_REPO / "channels_config").iterdir():
        if not folder.is_dir() or folder.name in {"aiwake", "shared"}:
            continue
        if folder.name.startswith((".", "_", "test")):
            continue
        if (folder / "master_dna.json").is_file():
            on_disk.append(folder.name)
    assert names == sorted(on_disk)
    assert ENGINE_ROOT == _REPO


def test_active_page_facade_matches_the_loader() -> None:
    env = os.environ.copy()
    env["ACTIVE_PAGE"] = "wonder_feed"
    env["OMNI_SKIP_DOTENV"] = "1"
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import production.writers.persona_dna as persona\n"
            "print(persona.ANNA_DISPLAY_NAME)\n"
            "print(persona.persona_context_block())\n",
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=_REPO,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    display, _, context = proc.stdout.partition("\n")
    expected = load_persona("wonder_feed")
    assert display == expected.display_name
    assert context.rstrip("\n") == expected.persona_context_block()
