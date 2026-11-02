import numpy as np

from products.animind_core import frameqa, illustrated_scene, mouthset, scene, script


def _blank_sheets(count: int) -> list[dict]:
    return [{"name": name, "quotes": []} for name in ("Ada", "Alan")[:count]]


def test_english_is_the_writer_default_and_flash_lite_is_the_paid_model():
    written = script.write(
        "solo", "", ["Ada"], sheets=_blank_sheets(1), use_llm=False,
    )
    assert written["topic"] == "artificial intelligence"
    assert written["lines"][0]["text"].startswith(("Today", "Artificial", "No joke", "Pay attention"))
    assert script.WRITER_MODEL == "gemini-3.5-flash-lite"
    assert written["scriptCost"] == {
        "provider": None, "model": None, "costUsd": 0.0,
        "inputTokens": 0, "outputTokens": 0, "cached": False, "status": "offline",
    }


def test_writer_reports_flash_lite_cost_metadata(monkeypatch):
    class Tracker:
        calls = 0

        def snapshot(self):
            self.calls += 1
            entries = [] if self.calls == 1 else [{
                "cost_usd": 0.0003, "input_tokens": 1000, "output_tokens": 500,
            }]
            return {"entries": entries}

    tracker = Tracker()
    monkeypatch.setattr(
        "production.cost.google_guardrail.get_google_cost_tracker", lambda: tracker,
    )
    monkeypatch.setattr(script, "_gemini", lambda _prompt: [])
    written = script.write(
        "solo", "machine creativity", ["Ada"],
        sheets=_blank_sheets(1), use_llm=True,
    )
    assert written["writer"] == "gemini"
    assert written["scriptCost"] == {
        "provider": "google", "model": "gemini-3.5-flash-lite",
        "costUsd": 0.0003, "inputTokens": 1000, "outputTokens": 500,
        "cached": False, "status": "ok",
    }


def test_fallback_debate_has_coherent_argument_language():
    written = script.write(
        "debate", "Should schools teach AI literacy?", ["Ada", "Alan"],
        sheets=_blank_sheets(2), use_llm=False, tone="educational",
        objective="debate", duration=60,
    )
    text = " ".join(line["text"] for line in written["lines"])
    assert written["scriptQA"]["pass"], written["scriptQA"]
    assert len(written["lines"]) == 13  # a 60 s target expands the compact nine-beat plan
    assert "Should schools teach AI literacy?" in written["lines"][0]["text"]
    assert "Cowardice" not in text and "pretty name" not in text
    assert "who said," not in text
    assert any(word in text.lower() for word in ("evidence", "premise", "test", "consequences"))


def _camera_scene() -> scene.Scene:
    lines = [
        {
            "speaker": index % 2,
            "beat": ("gancho", "tese", "réplica", "virada", "tréplica", "fechamento")[index],
            "kind": "quote" if index in (1, 3) else "paraphrase",
            "text": f"Argument {index}.",
            "startMs": index * 5200,
            "endMs": index * 5200 + 4800,
        }
        for index in range(6)
    ]
    return scene.Scene(
        template="debate", scenario="studio", rigs=[None, None], script=lines,
        envelope=np.ones(3200), env_fps=100, duration_s=31.5,
        disclaimer="Parody", names=["Ada", "Alan"], roles=["x", "y"],
    )


def test_camera_plan_is_deterministic_and_covers_debate_grammar():
    first, second = _camera_scene(), _camera_scene()
    assert first.camera_plan() == second.camera_plan()
    kinds = {shot["kind"] for shot in first.camera_plan()}
    assert {"wide", "cu", "single", "two", "react"} <= kinds
    assert all(
        before["startMs"] < after["startMs"]
        for before, after in zip(first.camera_plan(), first.camera_plan()[1:])
    )


def test_illustrated_scene_contract_places_face_overlays_without_generation():
    plate = illustrated_scene.IllustratedScene.from_dict({
        "image": "scene-with-blank-face.png", "width": 1920, "height": 1080,
        "faces": [{"x": 0.25, "y": 0.4, "scale": 0.2, "facing": "right"}],
    })
    box = plate.faces[0].overlay_box(
        (plate.width, plate.height), width_units=0.5, height_units=0.2,
        offset_units=(0.0, 0.15),
    )
    assert box[0] < 480 < box[2] and box[1] < 464 < box[3]
    capability = illustrated_scene.capability()
    assert capability["status"] == "contract-only" and capability["generation"] is False


def test_every_new_mouth_obeys_the_absolute_opening_aspect():
    colours = {
        "outline": (40, 30, 30, 255), "cavity": (42, 30, 34, 255),
        "teeth": (247, 244, 236, 255), "tongue": (190, 86, 92, 255),
        "lip": (142, 72, 78, 255),
    }
    mouths = {
        **mouthset.draw_set(90, colours, open_cap=42),
        **{f"emotion:{key}": value for key, value in mouthset.draw_expressions(90, colours, open_cap=42).items()},
    }
    for name, image in mouths.items():
        box = image.getchannel("A").getbbox()
        assert box is not None
        aspect = (box[3] - box[1]) / (box[2] - box[0])
        assert aspect <= mouthset.OPEN_ASPECT_CAP + 0.02, (name, aspect)
    assert frameqa.CAV_ABS == 0.50
