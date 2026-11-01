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


def test_default_inpaint_unchanged_without_hair():
    from products.animind_core.rig import _mouthless

    base = Image.new("RGBA", (200, 200), SKIN)
    ImageDraw.Draw(base).line((80, 120, 120, 120), fill=LIP, width=4)
    box, mouth = (40, 80, 160, 170), (75, 110, 125, 130)
    a = np.asarray(_mouthless(base, box, mouth, keep_hair=True))
    b = np.asarray(_mouthless(base, box, mouth, keep_hair=False))
    assert (a == b).all()  # no facial hair: same Telea fill as before
