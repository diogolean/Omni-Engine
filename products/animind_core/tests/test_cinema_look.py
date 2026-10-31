"""Cinematic look: zoom-dependent depth of field, subject vignette, eased push-ins, caption plate."""
import numpy as np
from PIL import Image

from products.animind_core import look, scene


def _scene(scenario: str = "arena") -> scene.Scene:
    lines = [{"speaker": 0, "beat": "gancho", "kind": "paraphrase", "text": "A verdade existe?", "startMs": 0, "endMs": 4000},
             {"speaker": 1, "beat": "tese", "kind": "quote", "text": "Não há fatos, só interpretações.", "startMs": 4300, "endMs": 9000,
              "quote": {"label": "Fragmentos", "source": "Fragmentos"}},
             {"speaker": 0, "beat": "réplica", "kind": "paraphrase", "text": "Então quem decide?", "startMs": 9300, "endMs": 12000},
             {"speaker": 1, "beat": "fechamento", "kind": "paraphrase", "text": "O público.", "startMs": 12300, "endMs": 14000}]
    return scene.Scene(template="debate", scenario=scenario, rigs=[None, None], script=lines, envelope=np.ones(1500) * 0.5,
                       env_fps=100, duration_s=14.5, disclaimer="Paródia", names=["Sócrates", "Nietzsche"], roles=["x", "y"])


def test_dof_is_continuous_and_grows_with_zoom():
    n = len(look.DOF_FAR)
    assert look.dof_level(1.0, n) == (0, 1, 0.0)
    assert look.dof_level(3.0, n)[0] == n - 1
    prev = -1.0
    for z in np.linspace(1.0, 2.3, 60):
        lo, hi, w = look.dof_level(float(z), n)
        u = lo + w
        assert u >= prev and (u - prev < 0.25 or prev < 0)  # no focus pops between nearby zooms
        prev = u


def test_close_up_background_is_softer_than_wide():
    sc = _scene()
    empty = Image.new("RGBA", (round(look.W * scene.SS), round(look.H * scene.SS)), (0, 0, 0, 0))

    def detail(zoom: float) -> float:
        g = np.asarray(sc._shoot(empty, look.Shot(900, 330, zoom, "cu"), (0.0, 0.0, 0.0), 0.0).convert("L"), np.float32)
        return float(np.abs(np.diff(g, axis=1)).mean())

    blurred = detail(2.1)
    assert abs(detail(1.04) - _sharp(sc, 1.04)) < 1e-6  # wide shots keep the set crisp
    assert blurred < 0.8 * _sharp(sc, 2.1)  # close-ups throw the set out of focus


def _sharp(sc: scene.Scene, zoom: float) -> float:
    far, mid = sc.far_dof, sc.mid_dof
    sc.far_dof, sc.mid_dof = [sc.far] * len(far), [sc.mid] * len(mid)
    try:
        empty = Image.new("RGBA", (round(look.W * scene.SS), round(look.H * scene.SS)), (0, 0, 0, 0))
        g = np.asarray(sc._shoot(empty, look.Shot(900, 330, zoom, "cu"), (0.0, 0.0, 0.0), 0.0).convert("L"), np.float32)
        return float(np.abs(np.diff(g, axis=1)).mean())
    finally:
        sc.far_dof, sc.mid_dof = far, mid


def test_vignette_follows_the_subject():
    field = look.vignette_field(0.6, (1280, 720))
    left = np.asarray(look.vignette_at(field, (400, 300)).convert("L"), np.float32)
    assert left.shape == (720, 1280)
    assert left[300, 400] >= 250 and left[300, 1270] < left[300, 10]
    centred = np.asarray(look.vignette_at(field, (640, 360)).convert("L"), np.float32)
    assert abs(float(centred[360, 5]) - float(centred[360, 1274])) <= 2


def test_bloom_only_lifts_highlights():
    img = Image.new("RGB", (200, 120), (40, 40, 40))
    img.paste((255, 255, 255), (90, 50, 110, 70))
    out = np.asarray(look.bloom(img, 0.3), np.float32)
    assert out[10, 10].max() <= 41  # shadows untouched
    assert out[60, 85].mean() > 42  # halo next to the highlight


def test_push_in_is_eased_with_no_jump_inside_a_shot():
    sc = _scene()
    for start, shot, _kind in sc.shots[1:]:
        z0 = sc.camera(start + 0.5)[0][1].zoom
        steps = [sc.camera(start + 0.5 + i / 24)[0][1].zoom for i in range(24)]
        assert steps[0] >= z0 - 1e-9 and max(np.diff(steps)) < 0.004
    cu = next((s, sh) for s, sh, _ in sc.shots if sh.kind == "cu")
    end = next(s for s, _sh, _ in sc.shots if s > cu[0])
    assert sc.camera(end - 0.01)[0][1].zoom > cu[1].zoom * 1.05  # quotes lean in further


def test_caption_plate_darkens_behind_text():
    sc = _scene()
    cap = sc._caption(sc.script[0])
    a = np.asarray(cap.getchannel("A"), np.float32)
    rows = a[a.shape[0] // 2:, cap.width // 2 - 200: cap.width // 2 + 200]
    assert np.median(rows) > 60  # a plate, not only a 5 px text shadow
    assert a[:, :20].max() < 5  # and it stays off the frame edges


def test_agora_set_is_a_lit_landscape_not_a_blank_plate():
    far, mid = look.plates("agora", "debate", [0.29, 0.71])
    win = np.asarray(far.crop((round(look.OX), round(look.OY), round(look.OX) + look.W, round(look.OY) + look.H)), np.float32)
    sky, floor = win[: int(look.H * 0.25)], win[int(look.H * 0.7):]
    assert win.std() > 25  # a built set (sky, hills, columns, floor), not a flat fill
    assert sky[..., 2].mean() > sky[..., 0].mean() - 40 and sky.mean() > 150  # bright daylight sky
    assert floor[..., 0].mean() > floor[..., 2].mean() + 20  # warm, sunlit stone underfoot
    left, right = win[:, : look.W // 3].mean(), win[:, -look.W // 3:].mean()
    assert left > right - 5  # the sun (key light) is on the left, like the rim of the left speaker
    assert np.asarray(mid)[..., 3].max() > 0  # mid layer carries light shafts, dust and the olive branch
    assert "agora" in scene.SCENARIOS and look.light_for("agora", "debate", 0, 0.29).side == -1


def test_agora_frame_has_set_and_lecterns():
    frame = np.asarray(_scene("agora").frame(5.0).convert("RGB"), np.float32)
    assert frame.std() > 25 and 40 < frame.mean() < 230
