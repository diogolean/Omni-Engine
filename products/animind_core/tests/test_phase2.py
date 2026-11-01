from pathlib import Path

import pytest

from animation.parametric import rhubarb
from products.animind_core import languages, script

from .test_api import H, client  # noqa: F401


def test_language_is_bcp47_open_and_defaults_to_english():
    assert languages.normalize(None) == "en-US"
    assert languages.normalize("pt") == "pt-BR"
    assert languages.profile("es-MX").tag == "es-MX"
    assert languages.profile("es-MX").script == "en-US"
    assert languages.recognizer("en") == "pocketSphinx"
    assert languages.recognizer("pt-BR") == "phonetic"
    written = script.write("solo", "", ["Ada"], lang="en-US", sheets=[{"quotes": []}], use_llm=False)
    assert written["topic"] == "artificial intelligence"
    assert written["lines"][0]["text"].startswith(("Today", "Artificial", "No joke", "Pay attention"))


def test_stick_presets_are_reusable_and_include_complete_face_sheet(client):  # noqa: F811
    catalog = client.get("/v1/stick-presets").json()
    assert {p["id"] for p in catalog["presets"]} == {"teacher", "sage", "rebel", "explorer"}
    body = {
        "type": "stick", "name": "Nina", "preset": "rebel",
        "palette": {"skin": [210, 160, 120], "outfit": [20, 90, 160]},
        "accessory": "bandana", "voiceId": "pt-BR-FranciscaNeural",
        "source": {"kind": "prompt", "prompt": "debatedora"},
    }
    first = client.post("/v1/characters", headers=H, json=body).json()
    second = client.post("/v1/characters", headers=H, json=body).json()
    changed_voice = client.post(
        "/v1/characters", headers=H,
        json={**body, "voiceId": "pt-BR-ThalitaMultilingualNeural"},
    ).json()
    assert first["id"] == second["id"] and second["reused"] is True
    assert changed_voice["id"] == first["id"] and changed_voice["reused"] is True
    assert changed_voice["voiceId"] == "pt-BR-ThalitaMultilingualNeural"
    assert first["preset"] == "rebel"
    assert first["gender"] == "female"
    assert first["voiceId"] == "pt-BR-FranciscaNeural"
    assert first["customization"]["accessory"] == "bandana"
    for view in first["views"].values():
        assert set(view["expressions"]) == {
            "neutral", "happy", "angry", "sad", "surprised", "thoughtful", "ironic",
        }
    path = first["reviewSheetUrl"].split("/v1/files/")[1]
    assert client.get(f"/v1/files/{path}").status_code == 200


def test_complex_rig_uses_the_same_emotional_mouth_bank(client):  # noqa: F811
    character = client.post(
        "/v1/characters", headers=H,
        json={"name": "Rita", "source": {"kind": "prompt", "prompt": "cartoon professor"}},
    ).json()
    assert character["type"] == "rig"
    for view in character["views"].values():
        assert set(view["expressions"]) == {
            "neutral", "happy", "angry", "sad", "surprised", "thoughtful", "ironic",
        }


def test_language_catalog_documents_recognizers(client):  # noqa: F811
    data = client.get("/v1/languages").json()
    assert data["default"] == "en-US" and data["openBcp47"] is True
    by_tag = {item["language"]: item for item in data["languages"]}
    assert by_tag["en-US"]["rhubarbRecognizer"] == "pocketSphinx"
    assert by_tag["pt-BR"]["rhubarbRecognizer"] == "phonetic"


def test_voice_endpoint_lists_gendered_language_specific_ids(client):  # noqa: F811
    en = client.get("/v1/voices?lang=en").json()
    pt = client.get("/v1/voices?lang=pt-BR").json()
    assert en["language"] == "en-US" and pt["language"] == "pt-BR"
    assert {voice["gender"] for voice in en["voices"]} == {"male", "female"}
    assert {voice["gender"] for voice in pt["voices"]} == {"male", "female"}
    assert {voice["voiceId"] for voice in en["voices"]}.isdisjoint(
        {voice["voiceId"] for voice in pt["voices"]}
    )


def test_rhubarb_rejects_unknown_recognizer():
    with pytest.raises(ValueError, match="phonetic or pocketSphinx"):
        rhubarb.analyze_visemes(Path("unused.wav"), recognizer="unknown", allow_download=False)
