import numpy as np

from products.animind_core import qa


def _face(lines):
    img = np.full((80, 120, 3), (240, 200, 175), np.uint8)
    for y in lines:
        img[y:y + 3, 30:90] = (150, 60, 60)
    return img


def test_ink_share_sees_a_leftover_lip_line():
    ref = _face([40])
    assert qa.ink_share(_face([]), ref, (35, 35, 85, 45)) == 0.0
    assert qa.ink_share(_face([40]), ref, (35, 35, 85, 45)) > 0.04


def test_stacked_lip_lines_are_a_second_mouth():
    one = qa.ink_mask(_face([40]), np.array([240, 200, 175]))
    two = qa.ink_mask(_face([30, 50]), np.array([240, 200, 175]))
    assert not qa._stacked(one, 50, 10)
    assert qa._stacked(two, 50, 10)
