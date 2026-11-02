"""Drawn mouth sets carry no mustache: the mouthless head must keep it and only repaint the lips below."""
import numpy as np
from PIL import Image, ImageDraw

SKIN, HAIR, LIP, COLLAR = (225, 180, 150, 255), (35, 32, 34, 255), (150, 80, 70, 255), (245, 245, 245, 255)


def _face():
    img = Image.new("RGBA", (300, 300), SKIN)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 250, 299, 299), fill=COLLAR)  # collar below the chin
    d.ellipse((60, 110, 240, 170), fill=HAIR)  # big mustache: the detector box covers it whole
    d.line((115, 180, 185, 180), fill=LIP, width=4)  # lip line just under it
    return img, (40, 90, 260, 260), (60, 110, 240, 185)  # image, mouth box, detected "mouth" (mustache + lips)


def test_keep_hair_keeps_mustache_and_erases_lips_only():
    from products.animind_core.rig import _facial_hair, _mouthless

    base, mbox, mouth = _face()
    src = np.asarray(base)
    out = np.asarray(_mouthless(base, mbox, mouth, keep_hair=True))
    hair = _facial_hair(src, mbox)
    assert hair[140, 150] and not hair[180, 150] and not hair[270, 150]
    assert (out[hair] == src[hair]).all()  # mustache untouched: no grey smear where it was
    lip = out[178:183, 120:180, :3].astype(int)
    assert np.abs(lip - np.array(SKIN[:3])).max() < 25  # lips repainted as skin
    assert (out[255:, :] == src[255:, :]).all()  # collar below keeps the master's art


def test_open_mouth_sits_below_a_walrus_mustache():
    import numpy as np
    from PIL import Image, ImageDraw

    from products.animind_core import frameqa
    from products.animind_core.scene import clear_mustache

    stache = Image.new("RGBA", (90, 46), (0, 0, 0, 0))
    ImageDraw.Draw(stache).ellipse((4, 4, 86, 40), fill=(32, 28, 28, 255))
    mouth = Image.new("RGBA", (70, 36), (0, 0, 0, 0))
    ImageDraw.Draw(mouth).ellipse((8, 6, 62, 30), fill=(48, 18, 20, 255))
    mouth_xy, stache_xy = (12, 8), (4, 0)
    drop, lift = clear_mustache(("mouth", mouth, mouth_xy, 10), ("mustache", stache, stache_xy, 11))
    assert drop > 0 or lift > 0
    canvas = Image.new("RGB", (160, 140), (232, 196, 168))
    canvas.paste(stache, (stache_xy[0], stache_xy[1] - lift), stache)
    canvas.paste(mouth, (mouth_xy[0], mouth_xy[1] + drop), mouth)
    box = (mouth_xy[0], mouth_xy[1] + drop, mouth_xy[0] + mouth.width, mouth_xy[1] + drop + mouth.height)
    measured = frameqa.measure(np.asarray(canvas), box)
    assert measured is not None
    assert measured["blobs"] == 1
    assert measured["cav"] >= frameqa.VISEME_CAV_MIN["F"] or measured["cav"] >= frameqa.VISEME_CAV_MIN["C"]


def test_debate_frame_loads_mouths_that_are_file_paths(tmp_path):
    import numpy as np
    from PIL import Image, ImageDraw

    from products.animind_core.scene import RigView, Scene

    head = Image.new("RGBA", (140, 180), (226, 186, 156, 255))
    mouth = Image.new("RGBA", (48, 28), (0, 0, 0, 0))
    ImageDraw.Draw(mouth).ellipse((6, 6, 42, 22), fill=(24, 12, 14, 255))
    closed = Image.new("RGBA", (48, 16), (0, 0, 0, 0))
    ImageDraw.Draw(closed).line((8, 8, 40, 8), fill=(150, 70, 64, 255), width=3)
    stache = Image.new("RGBA", (64, 22), (0, 0, 0, 0))
    ImageDraw.Draw(stache).ellipse((2, 2, 62, 18), fill=(28, 26, 26, 255))
    mouth_path, closed_path, stache_path = tmp_path / "E.png", tmp_path / "X.png", tmp_path / "mustache.png"
    mouth.save(mouth_path)
    closed.save(closed_path)
    stache.save(stache_path)
    rig = RigView(
        size=(140, 180),
        layers=[("head", head, (0, 0), 2), ("mustache", str(stache_path), (36, 96), 11)],
        visemes={"E": (str(mouth_path), (46, 100), 10), "X": (str(closed_path), (46, 108), 10)},
    )
    views = {"front": rig, "left": rig, "right": rig}
    script = [{
        "speaker": 1, "text": "Knowledge still needs a test.", "kind": "paraphrase", "beat": "réplica",
        "startMs": 0, "endMs": 2200, "view": "left", "visemes": [{"t": 0.0, "v": "E"}],
    }]
    scene = Scene(
        template="debate", scenario="studio", rigs=[views, views], script=script,
        envelope=np.ones(240), env_fps=100, duration_s=2.4, disclaimer="Parody",
        names=["Albert Einstein", "Friedrich Nietzsche"], roles=["Debate", "Debate"], lang="en-US",
    )
    frame = scene.frame(0.4)
    assert frame.size == (scene.OW, scene.OH)
    assert scene.trace[-1]["mouths"]


def test_flat_dark_oval_is_not_painted_on_the_frame():
    import numpy as np
    from PIL import Image, ImageDraw

    from products.animind_core import mouthset
    from products.animind_core.scene import RigView, Scene

    head = Image.new("RGBA", (180, 220), (230, 196, 168, 255))
    oval = Image.new("RGBA", (90, 54), (0, 0, 0, 0))
    ImageDraw.Draw(oval).ellipse((6, 8, 84, 46), fill=(10, 8, 8, 255))
    closed = Image.new("RGBA", (70, 16), (0, 0, 0, 0))
    ImageDraw.Draw(closed).line((8, 7, 62, 7), fill=(160, 80, 70, 255), width=3)
    assert mouthset.flat_dark_blob(oval)
    rig = RigView(
        size=(180, 220),
        layers=[("head", head, (0, 0), 2)],
        visemes={"E": (oval, (45, 120), 10), "X": (closed, (50, 130), 10)},
    )
    views = {"front": rig, "left": rig, "right": rig}
    script = [{
        "speaker": 0, "text": "Daydreams still sit on the facts.", "kind": "paraphrase", "beat": "argumento",
        "startMs": 0, "endMs": 2000, "view": "front", "visemes": [{"t": 0.0, "v": "E"}],
    }]
    scene = Scene(
        template="solo", scenario="studio", rigs=[views], script=script,
        envelope=np.ones(220), env_fps=100, duration_s=2.2, disclaimer="Parody",
        names=["Albert Einstein"], roles=["Talk"], lang="en-US",
    )
    frame = np.asarray(scene.frame(0.5).convert("RGB"))
    mouths = [item for item in scene.trace[-1]["mouths"] if item["v"] == "E"]
    assert mouths
    x0, y0, x1, y1 = (int(v) for v in mouths[0]["box"])
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(frame.shape[1], x1), min(frame.shape[0], y1)
    assert not mouthset.painted_mouth_is_flat(frame[y0:y1, x0:x1])


def test_default_inpaint_unchanged_without_hair():
    from products.animind_core.rig import _mouthless

    base = Image.new("RGBA", (200, 200), SKIN)
    ImageDraw.Draw(base).line((80, 120, 120, 120), fill=LIP, width=4)
    box, mouth = (40, 80, 160, 170), (75, 110, 125, 130)
    a = np.asarray(_mouthless(base, box, mouth, keep_hair=True))
    b = np.asarray(_mouthless(base, box, mouth, keep_hair=False))
    assert (a == b).all()  # no facial hair: same Telea fill as before
