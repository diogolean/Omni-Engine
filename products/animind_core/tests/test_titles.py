"""Film titles: cold-open title under a scope matte, act dips with chapter cards, end card, and the
key light that leans toward whoever has the floor in wide and two shots."""
import numpy as np
import pytest

from products.animind_core import scene, titles


def _line(sp, a, b, act, text="Fala."):
    return {"speaker": sp, "beat": "x", "kind": "paraphrase", "text": text, "startMs": a, "endMs": b, "act": act}


LINES = [_line(0, 1800, 5000, "abertura"), _line(1, 5300, 9000, "abertura"),
         _line(0, 9700, 13000, "confronto"), _line(1, 13300, 16000, "fechamento")]


def _scene(fmt="16:9", lines=LINES, duration=18.7):
    return scene.Scene(template="debate", scenario="agora", rigs=[None, None], script=lines, envelope=np.ones(2000) * 0.5,
                       env_fps=100, duration_s=duration, disclaimer="Paródia", names=["Sócrates", "Nietzsche"],
                       roles=["x", "y"], topic="a verdade", fmt=fmt)


def _luma(img):
    return np.asarray(img.convert("L"), np.float32)


def test_cold_open_has_scope_matte_and_title_in_the_lower_band():
    sc = _scene()
    assert sc.title_end == pytest.approx(1.8) and sc.matte == round((720 - 1280 / 2.39) / 2)
    g = _luma(sc.frame(1.0))
    assert g[: sc.matte - 2, 300:1000].max() < 5 and g[-sc.matte + 2:, :].max() < 5  # bars (the chip sits at left)
    assert g[sc.matte + 10: 400].mean() > 60  # the set shows through the window
    band = g[470: 720 - sc.matte, 400:880]
    assert band.max() > 235  # white title type sits under the faces, inside the window
    assert _luma(sc.frame(0.0)).mean() >= 0.8 * g.mean()  # thumbnail frame is the title, never black
    after = _luma(sc.frame(1.8 + titles.MATTE_EASE_S + 0.1))
    assert after[: sc.matte - 2, 300:1000].mean() > 40  # matte is gone once the voices run


def test_act_break_dips_but_never_to_black_and_cuts_inside_the_dip():
    sc = _scene()
    (i, act, a, b, numeral), = [x for x in sc.breaks if x[1] == "confronto"]
    assert (i, numeral) == (2, "II") and (a, b) == (9.0, 9.7)
    mid = (a + b) / 2
    assert titles.dip(mid, sc.dips) == pytest.approx(titles.DIP_FLOOR, abs=1e-3)
    bright, low = _luma(sc.frame(8.5)).mean(), _luma(sc.frame(mid)).mean()
    assert 0.25 * bright < low < 0.6 * bright
    assert any(abs(t - mid) < 1e-6 and kind == "cut" for t, _s, kind in sc.shots)
    assert titles.dip(5.1, sc.dips) == 1.0  # ordinary sentence gaps do not dip


def test_chapter_card_shows_after_the_cut_top_left_clear_of_faces():
    sc = _scene()
    with_card = _luma(sc.frame(9.35 + 1.0))
    sc.breaks = []
    without = _luma(sc.frame(9.35 + 1.0))
    diff = np.abs(with_card - without) > 12
    ys, xs = np.nonzero(diff)
    assert len(ys) > 500 and ys.min() >= 60 and ys.max() <= 140 and xs.max() < 420


def test_end_card_and_matte_return_after_the_last_line():
    sc = _scene()
    assert sc.end_start == pytest.approx(16.3)
    assert sc.matte_at(15.0) == 0.0 and sc.matte_at(17.5) == 1.0
    g = _luma(sc.frame(17.5))
    assert g[470: 720 - sc.matte, 400:880].max() > 230 and g[: sc.matte - 2, 300:1000].max() < 5


def test_vertical_has_no_matte_and_untitled_scripts_are_unchanged():
    assert _scene("9:16").matte == 0
    plain = [dict(x, startMs=x["startMs"] - 1800, endMs=x["endMs"] - 1800) for x in LINES[:2]]
    sc = _scene(lines=plain, duration=7.5)
    assert sc.title_end == 0.0 and sc.end_start is None and sc.matte_at(0.0) == 0.0


def test_close_up_keeps_headroom_and_the_name_card_below_the_chin():
    from products.animind_core import render

    sc = _scene()
    sc.script[2]["kind"] = "quote"
    shot = sc.shot_for(sc.script[2]["speaker"], 2)
    hair = scene.H * (scene.BUST_BOTTOM - scene.BUST_H)
    window_top = shot.cy - scene.H / (2 * shot.zoom)
    assert shot.kind == "cu"
    assert window_top <= hair - 0.04 * scene.H
    role = render.role_line({}, "debate", "Is knowledge imagination or only perspective", "en-US")
    assert role.lower().endswith("perspective")
    rows = sc._role_rows(role, 520)[1]
    assert "PERSPECTIVE" in " ".join(rows)
    assert not any(row.endswith("PERSPEC") for row in rows)
    card = sc._lower_third(0)
    assert sc.lower_third_y(shot, card.height) >= sc.OH - card.height - round(12 * sc.k)


def test_key_light_leans_toward_the_speaker_in_wide_shots():
    sc = _scene()
    wide = scene.look.Shot(640, 360, 1.0, "wide")
    assert sc._focus(wide, (0, 0, 0), 1.0)[0] == 640  # titles: centred
    left, right = sc._focus(wide, (0, 0, 0), 4.0)[0], sc._focus(wide, (0, 0, 0), 15.0)[0]
    assert left < 560 and right > 720
    xs = [sc._focus(wide, (0, 0, 0), t)[0] for t in np.arange(4.0, 7.0, 1 / 24)]
    assert max(abs(np.diff(xs))) < 24  # eased over a second, no light pops at the turn
