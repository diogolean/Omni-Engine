"""A stored puppet is recovered from its transparent original, not from a board."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient
from PIL import Image

H = {"Authorization": "Bearer test-key"}


def test_opaque_board_does_not_count_as_the_original(tmp_path, monkeypatch):
    monkeypatch.setenv("CORE_API_KEY", "test-key")
    monkeypatch.setenv("ANIMIND_DATA_DIR", str(tmp_path))
    root = tmp_path / "assets" / "puppets"
    real = root / "ada_real"
    (real / "views" / "front").mkdir(parents=True)
    (real / "views" / "left").mkdir()
    (real / "views" / "right").mkdir()
    for name in ("front", "left", "right"):
        (real / "views" / name / "view.json").write_text("{}", encoding="utf-8")
    (real / "rig.json").write_text("{}", encoding="utf-8")
    (real / "character.json").write_text(json.dumps({"id": "ada_real", "artStyle": "cartoon"}), encoding="utf-8")
    original = Image.new("RGBA", (32, 48), (0, 0, 0, 0))
    original.putpixel((1, 1), (10, 20, 30, 255))
    original.save(real / "source_original.png")
    board = root / "board_only"
    board.mkdir()
    (board / "rig.json").write_text("{}", encoding="utf-8")
    Image.new("RGB", (480, 620), (0, 0, 0)).save(board / "views_contact_sheet.png")
    from products.animind_core.app import app

    client = TestClient(app)
    listed = client.get("/v1/puppets", headers=H)
    assert listed.status_code == 200, listed.text
    body = listed.json()
    by_id = {row["id"]: row for row in body["puppets"]}
    assert by_id["ada_real"]["sourceOriginal"] == {"present": True, "transparent": True}
    assert by_id["ada_real"]["artStyle"] == "cartoon"
    assert by_id["ada_real"]["ready"] is True
    assert by_id["board_only"]["contactSheet"]["size"] == [480, 620]
    assert by_id["board_only"]["contactSheet"]["provesOriginal"] is False
    assert by_id["board_only"]["sourceOriginal"]["present"] is False
    assert by_id["board_only"]["ready"] is False
    assert any(row["path"].endswith("albert_einstein_ca8386e2") and row["exists"] is False for row in body["searched"])
