"""Einstein on the product path: a cached plate when it is valid, else the bust."""
import json
import os
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from products.animind_core import approval, avatar, characters, qa, render, rig, settings

_PAID = (
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "TOGETHER_API_KEY",
    "OPENROUTER_API_KEY",
    "ANIMIND_IMAGE_MODEL",
)


def _offline(monkeypatch, data: Path) -> None:
    monkeypatch.setenv("ANIMIND_DATA_DIR", str(data))
    monkeypatch.delenv("ANIMIND_FORCE_MOCK", raising=False)
    for key in _PAID:
        monkeypatch.delenv(key, raising=False)
    # The product search also looks in ~/.animind-core. Tests must not pick up
    # a plate installed on the machine that happens to run them.
    home = data / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", lambda: home)


def _refuse_image(monkeypatch) -> None:
    def refuse(*_args, **_kwargs):
        raise AssertionError("the Einstein product path must not call the image model")

    monkeypatch.setattr(
        "products.aiwake.animator.factory.create_puppet.generate_character_image",
        refuse,
    )
    monkeypatch.setattr(
        "products.aiwake.animator.factory.create_puppet.create_puppet",
        refuse,
    )


def _einstein(base: str = "http://test") -> dict:
    return characters.create_character(
        {
            "name": "Albert Einstein",
            "source": {"kind": "prompt", "prompt": "Albert Einstein"},
            "artStyle": "cartoon",
        },
        base,
    )


def test_build_rig_paints_einstein_when_gemini_is_off(tmp_path, monkeypatch):
    _offline(monkeypatch, tmp_path)
    _refuse_image(monkeypatch)
    skin = tmp_path / "skin"
    built = rig.build_rig("Albert Einstein", skin, lambda path: path.name)
    assert built["generator"] == "procedural"
    assert built["model"] == "portrait-einstein"
    assert built["qa"]["costUsd"] == 0.0
    assert built["qa"]["score"] == 100
    assert set(built["views"]) == {"front", "left", "right"}
    gates = qa.run(skin)
    for key in ("mouthAnchor", "landmarks", "mouthOpen", "skin"):
        assert gates[key]["pass"], gates[key]
    assert rig.EINSTEIN_PLATE_ID in built["note"]
    assert any(row["path"].endswith(rig.EINSTEIN_PLATE_ID) for row in built["plateSearch"])
    assert not any(row["ready"] for row in built["plateSearch"])


def test_product_character_renders_the_bust(tmp_path, monkeypatch):
    """POST /v1/characters offline uses the bust, and the render loader reads it."""
    _offline(monkeypatch, tmp_path)
    _refuse_image(monkeypatch)
    built = _einstein()
    assert built["generator"] == "procedural"
    assert built["status"] == "ready"
    assert "arte mock" not in (built.get("generatorNote") or "")
    assert rig.EINSTEIN_PLATE_ID in (built.get("generatorNote") or "")
    assert built["qa"]["score"] == 100
    assert built["qa"]["costUsd"] == 0.0
    for key in ("mouthAnchor", "landmarks", "mouthOpen", "skin"):
        assert key not in (built["qa"]["failed"] or [])
    skin = characters.puppets_dir() / built["id"]
    head = next(layer for layer in rig.load_view(skin, "front")["layers"] if layer["name"] == "head")
    plate = np.asarray(Image.open(head["url"]).convert("RGB"))
    assert float(plate.std()) > 18
    loaded = render._rig_views(built, "solo", 0)
    assert loaded is not None and set(loaded) == {"front", "left", "right"}
    scene, _track = approval.monologue(skin, name="Albert Einstein")
    assert (scene.OW, scene.OH) == (1280, 720)
    assert scene.duration_s >= 10.0
    cues = {cue["v"] for line in scene.script for cue in line["visemes"]}
    assert "X" in cues and len(cues) >= 3
    front = json.loads((skin / "views" / "front" / "view.json").read_text(encoding="utf-8"))
    anchor = front["mouthQA"]["anchor"]
    face = front["mouthQA"]["face"]
    width = max(1, int(face[2]) - int(face[0]))
    centre = (int(face[0]) + int(face[2])) / 2
    assert anchor["view"] == "front"
    assert abs((anchor["x"] - centre) / width) <= 0.01


def test_rerig_of_the_product_bust_does_not_create_puppet(tmp_path, monkeypatch):
    _offline(monkeypatch, tmp_path)
    _refuse_image(monkeypatch)
    built = _einstein()
    skin = characters.puppets_dir() / built["id"]
    again = avatar.rerig_from_plate(
        skin,
        description="Albert Einstein",
        url=lambda path: path.name,
        character_id=built["id"],
        name="Albert Einstein",
        edit=False,
    )
    assert again["qa"]["costUsd"] == 0.0
    assert again["frontShot"] == "facing_front"


def test_an_older_three_quarter_plate_is_reused_without_create_puppet(tmp_path, monkeypatch):
    """A complete cut is copied when the plate file is newer than the views.

    This plate's boxes already pass the front gate, so a left anchor tag is
    copied as filed. A front that fails symmetry is mirrored on the copy.
    """
    _offline(monkeypatch, tmp_path)
    _refuse_image(monkeypatch)
    plate = tmp_path / "assets" / "puppets" / rig.EINSTEIN_PLATE_ID
    rig.build_einstein_product(plate, lambda path: path.name)
    plate_file = rig._plate_file(plate)
    assert plate_file is not None
    front_path = plate / "views" / "front" / "view.json"
    doc = json.loads(front_path.read_text(encoding="utf-8"))
    anchor = doc["mouthQA"]["anchor"]
    face = doc["mouthQA"]["face"]
    anchor["view"] = "left"
    anchor["x"] = int(face[0]) + 8
    front_path.write_text(json.dumps(doc), encoding="utf-8")
    newer = plate_file.stat().st_mtime_ns + 5_000_000_000
    os.utime(plate_file, ns=(newer, newer))
    older = newer - 10_000_000_000
    for name in ("front", "left", "right"):
        spec = plate / "views" / name / "view.json"
        os.utime(spec, ns=(older, older))
    assert spec.stat().st_mtime_ns < plate_file.stat().st_mtime_ns
    assert rig._einstein_views_ready(plate)

    def refuse_paint(*_args, **_kwargs):
        raise AssertionError("a complete photographed cut must not be repainted")

    monkeypatch.setattr("products.animind_core.portrait.paint", refuse_paint)
    built = _einstein()
    assert built["generator"] == "cached-plate"
    assert built["model"] == rig.EINSTEIN_PLATE_ID
    assert "not a midline bust" in (built.get("generatorNote") or "")
    assert built["qa"]["costUsd"] == 0.0
    skin = characters.puppets_dir() / built["id"]
    kept = json.loads((skin / "views" / "front" / "view.json").read_text(encoding="utf-8"))
    assert kept["mouthQA"]["anchor"]["view"] == "left"
    again = avatar.rerig_from_plate(
        plate,
        description="Albert Einstein",
        url=lambda path: path.name,
        character_id=rig.EINSTEIN_PLATE_ID,
        name="Albert Einstein",
        edit=False,
    )
    assert again["qa"]["costUsd"] == 0.0
    assert again["frontShot"] == "facing_front"


def test_a_valid_cached_plate_is_copied_and_not_repainted(tmp_path, monkeypatch):
    _offline(monkeypatch, tmp_path)
    _refuse_image(monkeypatch)
    plate = tmp_path / "assets" / "puppets" / rig.EINSTEIN_PLATE_ID
    rig.build_einstein_product(plate, lambda path: path.name)
    marker = plate / "views" / "front" / "kept.txt"
    marker.write_text("photographed", encoding="utf-8")
    found, report = rig.locate_einstein_plate()
    assert found == plate
    assert any(row["ready"] and row["path"] == str(plate) for row in report)

    def refuse_paint(*_args, **_kwargs):
        raise AssertionError("a valid cached plate must not be repainted")

    monkeypatch.setattr("products.animind_core.portrait.paint", refuse_paint)
    built = _einstein()
    assert built["generator"] == "cached-plate"
    assert built["model"] == rig.EINSTEIN_PLATE_ID
    skin = characters.puppets_dir() / built["id"]
    assert skin != plate
    assert (skin / "views" / "front" / "kept.txt").read_text(encoding="utf-8") == "photographed"
    assert built["qa"]["costUsd"] == 0.0
    gates = qa.run(skin)
    for key in ("mouthAnchor", "landmarks", "mouthOpen", "skin"):
        assert gates[key]["pass"], gates[key]


def test_build_rig_still_asks_gemini_when_a_key_is_configured(tmp_path, monkeypatch):
    _offline(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "gemini_available", lambda: True)
    monkeypatch.setattr(rig, "locate_einstein_plate", lambda: (None, []))

    def generate_shots(*_args, **_kwargs):
        raise RuntimeError("paid plate")

    monkeypatch.setattr(rig, "generate_shots", generate_shots)
    with pytest.raises(RuntimeError, match="paid plate"):
        rig.build_rig("Albert Einstein", tmp_path / "paid", lambda path: path.name)


def test_a_dead_gemini_key_still_paints_einstein(tmp_path, monkeypatch):
    _offline(monkeypatch, tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    monkeypatch.setattr(settings, "gemini_available", lambda: True)

    def boom(*_args, **_kwargs):
        raise RuntimeError("402 RESOURCE_EXHAUSTED. Your prepayment credits are depleted.")

    monkeypatch.setattr(rig, "build_rig", boom)
    built = _einstein()
    assert built["generator"] == "procedural"
    assert built["qa"]["costUsd"] == 0.0
    assert "402" in (built.get("generatorNote") or "")
    assert rig.EINSTEIN_PLATE_ID in (built.get("generatorNote") or "")
    skin = characters.puppets_dir() / built["id"]
    assert qa.run(skin)["skin"]["pass"] is True


def test_is_einstein_matches_the_persona_not_every_albert():
    assert rig.is_einstein("Albert Einstein")
    assert rig.is_einstein("einstein")
    assert rig.is_einstein("young einstein parody")
    assert not rig.is_einstein("Albert Camus")
    assert not rig.is_einstein("a teacher")
