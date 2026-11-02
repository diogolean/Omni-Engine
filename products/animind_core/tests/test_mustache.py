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
    mouth_top = mouth_xy[1] + 6 + drop
    mouth_bot = mouth_xy[1] + 30 + drop
    stache_bot = stache_xy[1] + 40 - lift
    # The opening extends below the mustache, and the top still tucks under it.
    assert mouth_bot > stache_bot
    assert mouth_top < stache_bot
    plate = Image.new("RGB", mouth.size, (232, 196, 168))
    plate.paste(mouth, (0, 0), mouth)
    measured = frameqa.measure(np.asarray(plate), (0, 0, mouth.width, mouth.height))
    assert measured is not None and measured["blobs"] == 1 and measured["cav"] > 0.1


def test_a_chin_dot_grows_to_the_face_and_tucks_under_the_mustache():
    import numpy as np
    from PIL import Image, ImageDraw

    from products.animind_core.scene import scale_mouth_to_face, tuck_mustache_over_mouth

    face_w = 200
    dot = Image.new("RGBA", (80, 40), (0, 0, 0, 0))
    ImageDraw.Draw(dot).ellipse((36, 28, 44, 36), fill=(16, 10, 12, 255))
    grown = scale_mouth_to_face(dot, face_w, open_shape=True)
    box = grown.getchannel("A").getbbox()
    assert box is not None and box[2] - box[0] >= int(face_w * 0.28)
    slit = Image.new("RGBA", (90, 30), (0, 0, 0, 0))
    ImageDraw.Draw(slit).rectangle((10, 14, 80, 17), fill=(12, 8, 8, 255))
    opened = scale_mouth_to_face(slit, face_w, open_shape=True)
    opened_box = opened.getchannel("A").getbbox()
    assert opened_box[3] - opened_box[1] > 8
    head = Image.new("RGBA", (120, 80), (230, 196, 170, 255))
    ImageDraw.Draw(head).rectangle((20, 18, 100, 36), fill=(32, 28, 28, 255))
    mouth = Image.new("RGBA", (70, 40), (0, 0, 0, 0))
    ImageDraw.Draw(mouth).ellipse((8, 4, 62, 34), fill=(40, 16, 18, 255))
    tucked = np.asarray(tuck_mustache_over_mouth(mouth, head, (20, 16), (0, 0)))
    assert tuple(tucked[6, 30][:3]) == (32, 28, 28)


def test_stale_line_takes_are_replaced(tmp_path):
    import numpy as np

    from products.animind_core.render import sync_line_takes

    (tmp_path / "line_0.wav").write_bytes(b"old")
    (tmp_path / "line_2.wav").write_bytes(b"dropped")
    (tmp_path / "line_fit_4.wav").write_bytes(b"fit")
    (tmp_path / "line_fit_4.mp3").write_bytes(b"fit")
    pcm = np.zeros(240, np.int16)
    pcm[:10] = 1000
    sync_line_takes(tmp_path, [pcm, pcm])
    assert (tmp_path / "line_0.wav").stat().st_size > 100
    assert (tmp_path / "line_1.wav").is_file()
    assert not (tmp_path / "line_2.wav").exists()
    assert not (tmp_path / "line_fit_4.wav").exists()
    assert not (tmp_path / "line_fit_4.mp3").exists()


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
