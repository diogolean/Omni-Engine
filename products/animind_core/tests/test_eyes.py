from products.animind_core import eyes
from .test_api import H, _make, client  # noqa: F401


def test_blink_is_deterministic_and_mostly_open():
    states = [eyes.blink(i / 30, 7) for i in range(30 * 60)]
    assert states == [eyes.blink(i / 30, 7) for i in range(30 * 60)]
    assert states.count("open") > 0.85 * len(states)
    assert "closed" in states and "half" in states


def test_gaze_stays_in_unit_box_and_sometimes_hits_camera():
    pts = [eyes.gaze(i / 10, 3, toward=1, view="front", facing=0, camera_bias=0.3) for i in range(600)]
    assert all(-1 <= x <= 1 and -1 <= y <= 1 for x, y in pts)
    assert any(abs(x) < 0.05 and abs(y) < 0.05 for x, y in pts)
    assert sum(x > 0.3 for x, _ in pts) > 100  # mostly toward the other speaker


def test_character_has_eye_rig_and_review_sheet(client):  # noqa: F811
    c = _make(client)
    for view in c["views"].values():
        names = {layer["name"] for layer in view["layers"]}
        assert {"eye_white", "iris", "eye_socket", "eyes_half"} <= names
        assert len(view["eyeRig"]["irises"]) == 2 and view["eyeRig"]["maxShift"][0] > 0
    path = c["reviewSheetUrl"].split("/v1/files/")[1]
    assert client.get(f"/v1/files/{path}").headers["content-type"] == "image/png"
