"""Vector brows replace the painted stroke. Evil is a V. Sad is the inverse."""
import numpy as np
from PIL import Image, ImageDraw

from products.animind_core import brow_draw, human_eyes, qa, stick


def _stick_plate() -> Image.Image:
    image = Image.new("RGBA", (200, 80), (244, 218, 190, 255))
    draw = ImageDraw.Draw(image)
    draw.line((20, 30, 80, 26), fill=(35, 38, 44, 255), width=3)
    draw.line((120, 26, 180, 30), fill=(35, 38, 44, 255), width=3)
    return image


def _tapered_plate() -> tuple[Image.Image, list[tuple[int, int]]]:
    image = Image.new("RGBA", (180, 90), (232, 186, 156, 255))
    upper, lower = [], []
    for index, x in enumerate(range(30, 150)):
        t = index / 119
        thick = 3 + 12 * (1 - (2 * t - 1) ** 2)
        upper.append((x, 40 - thick / 2))
        lower.append((x, 40 + thick / 2))
    ImageDraw.Draw(image).polygon(upper + lower[::-1], fill=(48, 32, 24, 255))
    return image, upper + lower[::-1]


def _pair(model: dict, state: str) -> tuple[Image.Image | None, Image.Image | None]:
    drawn = brow_draw.render_state(model, state, 1.0)
    left = drawn["left"][0] if "left" in drawn else None
    right = drawn["right"][0] if "right" in drawn else None
    return left, right


def _column_span(sprite: Image.Image, frac: float) -> float:
    alpha = np.asarray(sprite.getchannel("A")) > 128
    _ys, xs = np.nonzero(alpha)
    x0, x1 = int(xs.min()), int(xs.max())
    x = int(round(x0 + (x1 - x0) * frac))
    column = np.nonzero(alpha[:, x])[0]
    if column.size == 0:
        return 0.0
    return float(column.max() - column.min() + 1)


def test_stick_brows_leave_the_head_clean_and_evil_is_not_sad():
    plate = _stick_plate()
    before = np.asarray(plate)
    ink = (before[..., 3] > 200) & (before[..., :3].mean(axis=2) < 80)
    lifted = brow_draw.lift(plate, style="stick")
    head = np.asarray(lifted["head"])
    assert head[ink][:, :3].mean() > 150
    assert lifted["model"]["style"] == "stick"
    assert qa.brow_still_in_head(lifted["head"], lifted["brow_left"], (0, 0), (0, 0))
    evil_l, evil_r = _pair(lifted["model"], "mau")
    sad_l, sad_r = _pair(lifted["model"], "triste")
    assert qa.brow_arch(evil_l, evil_r) == "v"
    assert qa.brow_arch(sad_l, sad_r) == "inverse"
    assert brow_draw.arch_delta(evil_l, "left") > brow_draw.arch_delta(sad_l, "left") + 4
    core = np.asarray(lifted["brow_left"])
    opaque = core[..., 3] > 200
    assert opaque.any()
    assert core[opaque][:, :3].std(axis=0).max() < 12


def test_painted_brow_under_the_layer_fails_the_head_gate():
    lifted = brow_draw.lift(_stick_plate(), style="stick")
    dirty = np.asarray(lifted["head"]).copy()
    brow = np.asarray(lifted["brow_left"].getchannel("A")) > 160
    dirty[brow, :3] = (20, 16, 14)
    dirty[brow, 3] = 255
    head = Image.fromarray(dirty)
    assert not qa.brow_still_in_head(head, lifted["brow_left"], (0, 0), (0, 0))
    faults = qa.human_avatar_faults(
        {"layers": [], "mouthQA": {}},
        head=head, brow=lifted["brow_left"], brow_xy=(0, 0), head_xy=(0, 0),
    )
    assert any("original brow" in fault for fault in faults)


def test_illustrated_brow_keeps_measured_taper_and_clears_the_head():
    plate, polygon = _tapered_plate()
    lifted = brow_draw.lift(plate, style="illustrated", polygons=[polygon])
    assert lifted["model"]["style"] == "illustrated"
    bare = lifted["head"].getpixel((90, 40))
    assert bare[0] > 80
    assert qa.brow_still_in_head(lifted["head"], lifted["brow_left"], (0, 0), (0, 0))
    sprite = lifted["brow_left"]
    tip = min(_column_span(sprite, 0.08), _column_span(sprite, 0.92))
    mid = _column_span(sprite, 0.5)
    assert mid >= tip + 4
    core = np.asarray(sprite)
    opaque = core[..., 3] > 200
    tone = np.median(core[opaque][:, :3], axis=0)
    assert abs(int(tone[0]) - 48) < 25
    evil_l, evil_r = _pair(lifted["model"], "evil")
    sad_l, sad_r = _pair(lifted["model"], "sad")
    assert qa.brow_arch(evil_l, evil_r) == "v"
    assert qa.brow_arch(sad_l, sad_r) == "inverse"


def test_stick_generator_draws_vector_brows_and_the_head_has_no_ink():
    spec = stick.PRESETS["teacher"]
    parts = stick._parts(
        spec["skin"], spec["outfit"], spec["accessory"],
        head_shape=spec["head"], brow_style=spec["brow"], build=spec["build"],
    )
    assert parts["browModel"]["style"] == "stick"
    head, origin = parts["head"]
    x, y = 276 - origin[0], 170 - origin[1]
    pixel = head.getpixel((x, y))
    assert pixel[3] > 200 and pixel[0] > 150
    evil_l, evil_r = _pair(parts["browModel"], "evil")
    sad_l, sad_r = _pair(parts["browModel"], "sad")
    assert qa.brow_arch(evil_l, evil_r) == "v"
    assert qa.brow_arch(sad_l, sad_r) == "inverse"


def test_human_build_redraws_the_brow_it_erases():
    plate = Image.new("RGBA", (160, 180), (0, 0, 0, 255))
    draw = ImageDraw.Draw(plate)
    draw.ellipse((30, 20, 130, 150), fill=(232, 186, 156, 255))
    brow = [(50, 36), (80, 30), (110, 36), (108, 42), (80, 40), (52, 42)]
    draw.polygon(brow, fill=(40, 28, 22, 255))
    eye = {"upper": [(48, 62), (64, 52), (80, 50), (96, 52), (112, 62)],
           "lower": [(48, 70), (64, 78), (80, 80), (96, 78), (112, 70)],
           "iris": (80, 66), "radius": 7}
    built = human_eyes.build(plate, eyes_geom=[eye], brows=[brow])
    assert built["browModel"]["style"] == "illustrated"
    assert built["head"].getpixel((80, 32))[0] > 80
    assert qa.brow_still_in_head(built["head"], built["brow_left"], (0, 0), (0, 0))
    evil_l, _evil_r = _pair(built["browModel"], "evil")
    sad_l, _sad_r = _pair(built["browModel"], "sad")
    assert qa.brow_arch(evil_l, None) == "v"
    assert qa.brow_arch(sad_l, None) == "inverse"


def test_scene_brow_follows_emotion_while_the_mouth_stays_on_the_viseme():
    from products.animind_core import scene as scene_mod
    from products.animind_core.scene import RigView, Scene

    plate = _stick_plate()
    lifted = brow_draw.lift(plate, style="stick")
    dot = Image.new("RGBA", (2, 2), (0, 0, 0, 0))
    mouth = Image.new("RGBA", (12, 6), (0, 0, 0, 0))
    ImageDraw.Draw(mouth).line((1, 3, 10, 3), fill=(150, 70, 64, 255), width=2)

    def paint(emotion: str) -> tuple[np.ndarray, dict, dict]:
        rig = RigView(
            size=plate.size,
            layers=[
                ("head", lifted["head"], (0, 0), 2),
                ("brow_left", dot, (0, 0), 7),
                ("brow_right", dot, (0, 0), 7),
            ],
            visemes={"X": (mouth, (80, 50), 10)},
            brow_model=lifted["model"],
            px_scale=1.0,
        )
        views = {"front": rig, "left": rig, "right": rig}
        script = [{
            "speaker": 0, "text": "A brow can move without the mouth.", "kind": "paraphrase",
            "beat": "argumento", "emotion": emotion, "startMs": 0, "endMs": 2000,
            "view": "front", "visemes": [{"t": 0.0, "v": "X"}],
        }]
        scene = Scene(
            template="solo", scenario="studio", rigs=[views], script=script,
            envelope=np.ones(220), env_fps=100, duration_s=2.2, disclaimer="Parody",
            names=["Lia"], roles=["Talk"], lang="en-US",
        )
        stage = Image.new("RGBA", (round(scene_mod.W * scene_mod.SS), round(scene_mod.H * scene_mod.SS)), (0, 0, 0, 0))
        scene._draw_char(stage, 0, 0.5, 0.4, ("X", "X", 1.0), False)
        return np.asarray(stage), scene._face_log[-1], scene._mouth_log[-1]

    evil, evil_face, evil_mouth = paint("mau")
    sad, sad_face, sad_mouth = paint("triste")
    assert evil_mouth["v"] == "X" and sad_mouth["v"] == "X"
    assert evil_face["emotion"] == "mau" and sad_face["emotion"] == "triste"
    assert any(abs(v) > 0.5 for v in evil_face["brows"])
    assert int(np.abs(evil.astype(int) - sad.astype(int)).sum()) > 5000
