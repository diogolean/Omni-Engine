"""Mood sheet and monologue: brows and eyes follow the line, the viseme does not."""
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from products.animind_core import approval, brow_draw, human_eyes, qa, render, rig, stick


def _diff(a, b, box: tuple[int, int, int, int] | None = None) -> int:
    left, right = (a, b) if box is None else (a.crop(box), b.crop(box))
    return int(np.abs(
        np.asarray(left.convert("RGB")).astype(np.int16)
        - np.asarray(right.convert("RGB")).astype(np.int16)
    ).sum())


@pytest.fixture(scope="module")
def skins(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    approval.clear_paid_keys()
    root = tmp_path_factory.mktemp("emotion-approval")
    return {
        "stick": approval.build_stick(root / "stick"),
        "illustrated": approval.build_illustrated(root / "illustrated"),
    }


def test_beats_cover_ten_seconds_and_three_mood_changes():
    starts = [row[0] for row in approval.BEATS]
    ends = [row[1] for row in approval.BEATS]
    emotions = [row[2] for row in approval.BEATS]
    assert ends[-1] - starts[0] >= 9.5
    assert ends[-1] <= 12.0
    assert len(emotions) >= 4
    assert sum(a != b for a, b in zip(emotions, emotions[1:])) >= 3
    cues = {vis for _t, vis in render.envelope_visemes(approval._pcm(2.4))}
    assert "X" in cues and len(cues) >= 3


@pytest.mark.parametrize("kind", ("stick", "illustrated"))
def test_approval_gates_pass_and_the_mood_row_keeps_one_mouth(skins: dict[str, Path], kind: str):
    skin = skins[kind]
    gates = approval.approval_gates(skin)
    failed = [name for name, gate in gates.items() if not gate["pass"]]
    assert failed == []
    view = rig.load_view(skin, "front")
    model = view["browModel"]
    arches = {}
    for state in ("evil", "happy", "sad", "perplexed"):
        drawn = brow_draw.render_state(model, state, 1.0)
        arches[state] = qa.brow_arch(drawn["left"][0], drawn["right"][0])
    assert arches["evil"] == "v"
    assert arches["sad"] == "inverse"
    assert arches["perplexed"] == "other"
    assert arches["happy"] == "happy"
    moods = {state: approval.mood_frame(skin, state) for state in ("neutral", "evil", "happy", "sad")}
    mouth = view["visemes"]["C"]
    box = (mouth["x"], mouth["y"], mouth["x"] + mouth["w"], mouth["y"] + mouth["h"])
    assert _diff(moods["evil"], moods["happy"], box) == 0
    assert _diff(moods["sad"], moods["happy"], box) == 0
    assert _diff(moods["neutral"], moods["evil"]) > 50_000
    face = moods["evil"].size
    band = (0, 0, face[0], face[1] // 2)
    assert _diff(moods["evil"], moods["happy"], band) > 20_000
    assert brow_draw.eye_lids("evil") == "half"
    assert brow_draw.eye_lids("happy") == "open"
    assert brow_draw.gaze_bias("happy")[1] < brow_draw.gaze_bias("evil")[1]


@pytest.mark.parametrize("kind", ("stick", "illustrated"))
def test_monologue_squints_and_looks_with_the_mood_while_the_viseme_stays(skins: dict[str, Path], kind: str):
    skin = skins[kind]
    scene, _track = approval.monologue(skin, viseme="C")
    assert scene.duration_s >= 10.0
    gazes = {}
    for start, end, emotion, _text in approval.BEATS:
        t = approval.open_time(start, end)
        scene.frame(t)
        face = scene.trace[-1]["faces"][0]
        mouth = scene.trace[-1]["mouths"][0]
        assert face["emotion"] == emotion
        assert mouth["v"] == "C"
        assert face["blink"] == brow_draw.eye_lids(emotion)
        base_x, base_y = scene.gaze(0, t, "front")
        _name, blend = scene._emotion(0, t)
        bias_x, bias_y = brow_draw.gaze_bias(emotion)
        assert face["gaze"][0] == round(max(-1.0, min(1.0, base_x + bias_x * blend)), 3)
        assert face["gaze"][1] == round(max(-1.0, min(1.0, base_y + bias_y * blend)), 3)
        gazes[emotion] = face["gaze"]
    assert gazes["alegre"][1] < gazes["mau"][1] - 0.5
    assert gazes["triste"][1] > gazes["neutro"][1] + 0.4


def test_stick_build_passes_the_mouth_gates_without_a_later_legalize(tmp_path: Path):
    """The product generator seats and opens the mouths. A fixture pass is not required."""
    skin = tmp_path / "teacher"
    stick.build(skin, lambda path: str(path), preset="teacher")
    gates = qa.run(skin)
    failed = [name for name in qa.APPROVAL if not gates[name]["pass"]]
    assert failed == [], {name: gates[name].get("note") or gates[name].get("bad") for name in failed}
    ratios = gates["mouthOpen"]["ratios"]
    assert ratios["D"] >= 1.35
    assert ratios["C"] >= 1.25
    assert ratios["H"] >= 1.20
    for share in gates["mouthOpen"]["expressionFaceShare"].values():
        assert share <= qa.EXPRESSION_OPEN_ABS + qa.OPEN_SLACK


def test_product_mouths_match_the_cheek_and_einstein_clears_skin(tmp_path: Path):
    """The skin gate is measured. A lip fringe recoloured to the cheek passes at ΔE 6.0."""
    approval.clear_paid_keys()
    assert qa.LIMITS["skinDeltaE"] == 6.0
    teacher = tmp_path / "teacher"
    stick.build(teacher, lambda path: str(path), preset="teacher")
    stick_skin = qa.skin_delta_e(teacher / "views" / "front")
    assert stick_skin["pass"] is True
    assert stick_skin["value"] <= 6.0
    assert stick_skin["limit"] == 6.0
    assert "drawn" not in (stick_skin.get("note") or "")
    tagged = json.loads((teacher / "views" / "front" / "view.json").read_text(encoding="utf-8"))
    assert (tagged.get("mouthQA") or {}).get("mouths") not in {"human", "aiwake drawn set"}
    skin = approval.build_einstein(tmp_path / "einstein")
    gates = qa.run(skin)
    assert gates["skin"]["pass"] is True, gates["skin"]
    assert gates["skin"]["value"] <= qa.LIMITS["skinDeltaE"]
    assert "drawn" not in (gates["skin"].get("note") or "")
    failed = [name for name in qa.APPROVAL if not gates[name]["pass"]]
    assert failed == [], {name: gates[name] for name in failed}
    assert qa.score(gates) == 100
    head = next(layer for layer in rig.load_view(skin, "front")["layers"] if layer["name"] == "head")
    plate = np.asarray(Image.open(skin / "views" / "front" / head["url"]).convert("RGB"))
    assert float(plate.std()) > 18


def test_illustrated_vowels_open_past_the_rest_mouth(tmp_path: Path):
    """Phase 26 left the illustrated vowel at about 1.04× the rest lip."""
    skin = tmp_path / "face"
    approval.build_illustrated(skin)
    face = json.loads((skin / "rig.json").read_text(encoding="utf-8"))["boxes"]["face"]
    gate = qa.mouth_open(skin / "views" / "front", face)
    assert gate["pass"], gate.get("bad")
    assert gate["ratios"]["D"] >= 1.35
    assert gate["ratios"]["C"] >= 1.25
    assert gate["ratios"]["H"] >= 1.20
    for share in gate["expressionFaceShare"].values():
        assert share <= qa.EXPRESSION_OPEN_ABS + qa.OPEN_SLACK


def test_speech_viseme_x_is_not_replaced_by_the_expression(skins: dict[str, Path]):
    skin = skins["stick"]
    scene, _track = approval.monologue(skin)
    seen_x = False
    for line in scene.script:
        for cue in line["visemes"]:
            if cue["v"] != "X":
                continue
            t = float(cue["t"]) + 0.02
            if not (line["startMs"] / 1000 <= t <= line["endMs"] / 1000):
                continue
            scene.frame(t)
            assert scene.trace[-1]["mouths"][0]["v"] == "X"
            seen_x = True
            break
        if seen_x:
            break
    assert seen_x
    scene.frame(2.60)
    assert scene.trace[-1]["mouths"][0]["v"].startswith("X_")


def test_illustrated_still_uses_the_brow_model_and_one_viseme_mark(skins: dict[str, Path]):
    built = human_eyes.load(skins["illustrated"])
    assert built is not None and built.get("browModel")
    evil = human_eyes.still_cell(built, "evil")
    happy = human_eyes.still_cell(built, "happy")
    width, height = evil.size
    assert _diff(evil, happy, (0, 120, width, 280)) > 5_000
    assert _diff(evil, happy, (width // 2 - 40, 320, width // 2 + 40, 380)) == 0
