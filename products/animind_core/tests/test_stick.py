import json

from products.animind_core import eyes, frameqa, script

from .test_api import H, client  # noqa: F401


def test_stick_is_first_class_and_uses_no_image_model(client):  # noqa: F811
    response = client.post(
        "/v1/characters",
        headers=H,
        json={"type": "stick", "name": "Lia", "source": {"kind": "prompt", "prompt": "professora"}},
    )
    assert response.status_code == 200, response.text
    character = response.json()
    assert character["type"] == "stick"
    assert character["generator"] == "procedural"
    assert character["qa"]["score"] == 100
    for view in character["views"].values():
        assert view["procedural"]
        assert set(view["visemes"]) == set("XABCDEFGH")
        names = {layer["name"] for layer in view["layers"]}
        assert {"body", "arm_left", "arm_right", "head", "eye_white", "iris", "eye_socket",
                "eyes_half", "eyes_closed", "brow_left", "brow_right"} <= names
        assert all(layer["url"].startswith("http") for layer in [*view["layers"], *view["visemes"].values()])


def test_stick_mouths_are_distinct_and_opening_is_limited(client):  # noqa: F811
    character = client.post(
        "/v1/characters", headers=H,
        json={"type": "stick", "name": "Milo", "source": {"kind": "prompt", "prompt": "aluno"}},
    ).json()
    mouths = character["views"]["front"]["visemes"]
    alpha_boxes = {}
    for code, layer in mouths.items():
        path = layer["url"].split("/v1/files/")[1]
        image_path = client.get(f"/v1/files/{path}")
        assert image_path.status_code == 200
        alpha_boxes[code] = (layer["w"], layer["h"])
    assert set(alpha_boxes) == set("XABCDEFGH")
    assert max(height for _width, height in alpha_boxes.values()) <= 88


def test_blink_timing_rate_and_occasional_double():
    fps = 24
    states = [eyes.blink(i / fps, 7) for i in range(fps * 120)]
    starts = [i for i in range(1, len(states)) if states[i - 1] == "open" and states[i] == "half"]
    rate = len(starts) / 2
    assert frameqa.BLINK_RATE_MIN <= rate <= frameqa.BLINK_RATE_MAX
    assert any(b - a < fps for a, b in zip(starts, starts[1:]))  # deterministic occasional double
    assert "closed" in states and "half" in states


def test_frameqa_face_gate_checks_blinks_and_emotional_brows():
    frames = []
    for index in range(24 * 12):
        t = index / 24
        emotion = "alegre" if t < 6 else "bravo"
        frames.append({
            "t": round(t, 3),
            "faces": [{"char": 0, "blink": eyes.blink(t, 7), "emotion": emotion,
                       "brows": [-2, -2, -4, 4] if emotion == "alegre" else [2, 2, 12, -12]}],
        })
    bad, report = frameqa.facial_check({"frames": frames})
    assert not bad
    assert frameqa.BLINK_RATE_MIN <= report["blinkRatesPerMin"][0] <= frameqa.BLINK_RATE_MAX
    frozen = json.loads(json.dumps(frames))
    for frame in frozen:
        frame["faces"][0]["brows"] = [0, 0, 0, 0]
    bad, _report = frameqa.facial_check({"frames": frozen})
    assert any("brows frozen" in reason for _frame, reason in bad)


def test_offline_writer_uses_tone_objective_duration_and_n_speakers():
    names = ["Ana", "Beto", "Caio"]
    sheets = [{"quotes": []} for _ in names]
    out = script.write(
        "dialogue", "energia solar", names, sheets=sheets, use_llm=False,
        tone="educacional", objective="ensinar", duration=30,
    )
    assert out["writer"] == "template"
    assert out["tone"] == "educacional" and out["objective"] == "ensinar"
    assert out["targetDurationS"] == 30
    assert {line["speaker"] for line in out["lines"]} == {0, 1, 2}
    assert "Passo a passo" in out["lines"][0]["text"]
    assert "vamos entender" in out["lines"][0]["text"].lower()
