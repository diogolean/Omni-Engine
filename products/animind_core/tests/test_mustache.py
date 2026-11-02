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


def test_open_viseme_sits_on_the_rest_mouth():
    """X is already on the real mouth. G moves so its top ink row matches X, not the coat or the eyes."""
    from PIL import Image, ImageDraw

    from products.animind_core.scene import RigView, _ink_top, rest_lip_drop

    rest = Image.new("RGBA", (48, 24), (0, 0, 0, 0))
    ImageDraw.Draw(rest).rectangle((6, 8, 40, 14), fill=(150, 70, 64, 255))
    opened = Image.new("RGBA", (48, 36), (0, 0, 0, 0))
    ImageDraw.Draw(opened).rectangle((6, 18, 40, 32), fill=(40, 16, 18, 255))
    rest_xy, open_xy = (20, 100), (18, 40)
    rv = RigView(
        size=(120, 160),
        layers=[],
        visemes={"X": (rest, rest_xy, 10), "G": (opened, open_xy, 10)},
    )
    drop = rest_lip_drop(rv, ("mouth", opened, open_xy, 10), face_w=120)
    assert _ink_top(rest) == 8 and _ink_top(opened) == 18
    assert drop == (rest_xy[1] + 8) - (open_xy[1] + 18)
    assert rest_xy[1] + 8 == open_xy[1] + 18 + drop
    assert rest_lip_drop(rv, ("mouth", rest, rest_xy, 10), face_w=120) == 0


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


def test_white_mustache_stays_and_only_the_lips_are_filled():
    """A light moustache is hair, not a dark blob. The lip line is what gets inpainted."""
    from products.animind_core.rig import _facial_hair, _mouthless

    img = Image.new("RGBA", (220, 260), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((30, 20, 190, 230), fill=(226, 176, 146, 255))
    draw.ellipse((55, 118, 165, 158), fill=(181, 168, 161, 255))  # warm-grey moustache, sat ~0.11
    draw.rectangle((78, 168, 142, 182), fill=(168, 64, 72, 255))  # lips
    draw.rectangle((20, 230, 200, 259), fill=(245, 245, 245, 255))  # collar, not hair
    box = (40, 100, 180, 220)
    hair = _facial_hair(np.asarray(img), box)
    assert hair[130, 110] and not hair[174, 110] and not hair[244, 110]
    out = np.asarray(_mouthless(img, box, (70, 160, 150, 190), keep_hair=True))
    assert tuple(out[130, 110][:3]) == (181, 168, 161)
    assert tuple(out[174, 110][:3]) != (168, 64, 72)


def test_f_viseme_is_not_grown_to_a_full_open_mouth():
    from products.animind_core.scene import F_FACE_SHARE, _MOUTH_OF_FACE, scale_mouth_to_face

    face_w = 200
    hole = Image.new("RGBA", (80, 40), (0, 0, 0, 0))
    ImageDraw.Draw(hole).ellipse((28, 12, 52, 28), fill=(40, 16, 18, 255))  # 24px, under 0.16 of the face
    full = scale_mouth_to_face(hole, face_w, open_shape=True)
    half = scale_mouth_to_face(hole, round(face_w * F_FACE_SHARE), open_shape=True)
    full_w = full.getchannel("A").getbbox()
    half_w = half.getchannel("A").getbbox()
    assert full_w[2] - full_w[0] >= int(face_w * _MOUTH_OF_FACE) - 4
    assert half_w[2] - half_w[0] < int(face_w * 0.22)
    assert F_FACE_SHARE == 0.55


def test_tall_close_up_keeps_the_mouth_above_captions_and_the_crown_in_frame():
    import numpy as np
    from PIL import Image, ImageDraw

    from products.animind_core import scene

    rest = Image.new("RGBA", (48, 28), (0, 0, 0, 0))
    ImageDraw.Draw(rest).rectangle((4, 4, 44, 24), fill=(150, 70, 64, 255))
    rig = scene.RigView(size=(280, 1000), layers=[], visemes={"X": (rest, (90, 772), 10)})
    views = {"front": rig, "left": rig, "right": rig}
    line = {"speaker": 0, "kind": "quote", "text": "Judgment needs a witness.", "beat": "tese",
            "startMs": 0, "endMs": 3000}
    sc = scene.Scene(
        template="debate", scenario="studio", rigs=[views, views], script=[line, line, line, line, line],
        envelope=np.ones(40), env_fps=10, duration_s=4.0, disclaimer="Parody",
        names=["Albert Einstein", "Friedrich Nietzsche"], roles=["Debate", "Debate"], lang="en-US",
    )
    bare = scene.Scene(
        template="debate", scenario="studio", rigs=[None, None], script=sc.script,
        envelope=sc.env, env_fps=10, duration_s=4.0, disclaimer="Parody",
        names=sc.names, roles=["Debate", "Debate"], lang="en-US",
    )
    shot = sc.shot_for(0, 2)
    plain = bare.shot_for(0, 2)
    assert shot.kind == "cu" and plain.kind == "cu"
    assert shot.cy > plain.cy
    z = scene.look.clamp_shot(shot).zoom * (1 + scene.PUSH_CU)
    mouth_y = (scene.H * scene.BUST_BOTTOM * scene.SS - rig.size[1] + 772 + rest.height) / scene.SS
    head_top = (scene.H * scene.BUST_BOTTOM * scene.SS - rig.size[1]) / scene.SS
    mouth_screen = scene.H / 2 + (mouth_y - shot.cy) * z
    crown_screen = scene.H / 2 + (head_top - shot.cy) * z
    assert mouth_screen <= sc.cap_y - 64 + 1.5
    assert crown_screen >= 0
    assert shot.zoom < plain.zoom
    vertical = scene.Scene(
        template="debate", scenario="studio", rigs=[views, views], script=sc.script,
        envelope=sc.env, env_fps=10, duration_s=4.0, disclaimer="Parody",
        names=sc.names, roles=["Debate", "Debate"], lang="en-US", fmt="9:16",
    )
    assert vertical.shot_for(0, 2).cy == scene.vertical_cy(scene.V_CU_ZOOM, scene.V_CU_FACE_AT)


def test_rerig_front_from_the_cached_master_keeps_a_white_mustache(tmp_path, monkeypatch):
    import json

    from products.animind_core import rig, settings

    monkeypatch.setenv("ANIMIND_DATA_DIR", str(tmp_path))
    skin = tmp_path / "assets" / "puppets" / "albert"
    skin.mkdir(parents=True)
    master = Image.new("RGBA", (320, 420), (0, 0, 0, 0))
    draw = ImageDraw.Draw(master)
    draw.rounded_rectangle((70, 210, 250, 430), 24, fill=(36, 64, 120, 255))
    draw.rectangle((140, 180, 180, 230), fill=(226, 176, 146, 255))
    draw.ellipse((90, 40, 230, 220), fill=(232, 186, 156, 255))
    draw.ellipse((118, 150, 202, 188), fill=(181, 168, 161, 255))
    draw.rectangle((140, 192, 180, 206), fill=(168, 64, 72, 255))
    for cx in (140, 180):
        draw.ellipse((cx - 12, 100, cx + 12, 124), fill=(248, 248, 246, 255))
        draw.ellipse((cx - 5, 106, cx + 5, 118), fill=(30, 24, 22, 255))
    master.save(skin / "character_front.png")
    (skin / "rig_front.json").write_text(json.dumps({
        "boxes": {
            "face": [90, 40, 230, 230],
            "left_eye": [128, 100, 152, 124],
            "right_eye": [168, 100, 192, 124],
            "mouth": [118, 148, 202, 210],
        },
    }), encoding="utf-8")
    (skin / "character.json").write_text(json.dumps({
        "id": "albert", "name": "Albert Einstein", "views": {}, "viewStatus": {},
    }), encoding="utf-8")
    built = rig.rerig_front_from_master(skin, lambda path: path.name)
    names = [layer["name"] for layer in built["views"]["front"]["layers"]]
    assert "mustache" in names
    stache = Image.open(skin / "views" / "front" / "mustache.png").convert("RGBA")
    assert stache.getchannel("A").getextrema()[1] > 128
    rig.main(["rerig-front", "albert"])
    saved = json.loads((skin / "character.json").read_text(encoding="utf-8"))
    assert saved["viewStatus"]["front"].startswith("re-rigged from the cached master")
    assert settings.data_dir() == tmp_path


def test_default_inpaint_unchanged_without_hair():
    from products.animind_core.rig import _mouthless

    base = Image.new("RGBA", (200, 200), SKIN)
    ImageDraw.Draw(base).line((80, 120, 120, 120), fill=LIP, width=4)
    box, mouth = (40, 80, 160, 170), (75, 110, 125, 130)
    a = np.asarray(_mouthless(base, box, mouth, keep_hair=True))
    b = np.asarray(_mouthless(base, box, mouth, keep_hair=False))
    assert (a == b).all()  # no facial hair: same Telea fill as before
