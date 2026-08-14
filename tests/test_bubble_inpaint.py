"""Guards for the speech-bubble erase in Stage 5 (_apply_inpaint).

Two defects measured on cap-shield-broken, 2026-08-13:
  * LaMa returns a reconstruction of the WHOLE crop and it was adopted wholesale — 22-49% of
    a panel's pixels changed while the text mask covered ~1%, so the artwork was silently
    re-rendered every time one bubble was cleaned.
  * A bubble's text bbox hugs the bubble, and just outside it is dark art, so LaMa continued
    that art into the bubble and painted it black (region mean 61 -> 10) — visibly worse than
    leaving the dialogue readable.
"""
import numpy as np
import pytest

from stages.stage_5.shots import _apply_inpaint


def _panel(bg=200):
    """A crop with a white 'bubble' at (10,10,30,20) sitting on mid-tone art."""
    img = np.full((80, 80, 3), bg, np.uint8)
    img[10:30, 10:40] = 255
    img[16:24, 14:36] = 20          # the lettering
    return img


def test_pixels_outside_the_boxes_are_untouched():
    """The whole point: cleaning a bubble must not re-render the artwork around it."""
    crop = _panel()
    filled = np.full_like(crop, 7)          # a fill that differs everywhere
    out = _apply_inpaint(crop, filled, [{"x": 10, "y": 10, "w": 30, "h": 20}])

    outside = np.ones(crop.shape[:2], bool)
    outside[8:32, 8:42] = False             # box + the 2px paste margin
    assert np.array_equal(out[outside], crop[outside])


def test_a_believable_fill_is_pasted_inside_the_box():
    crop = _panel()
    filled = crop.copy()
    filled[10:30, 10:40] = 250              # bubble wiped to paper — plausible
    out = _apply_inpaint(crop, filled, [{"x": 10, "y": 10, "w": 30, "h": 20}])
    assert out[20, 25].mean() > 200, "the lettering should be gone"


def test_a_black_hallucination_is_rejected_for_flat_paper():
    """The measured failure: LaMa painted a white bubble black. Anything that far from the
    region's own background is a hallucination, and a bubble's interior is flat paper anyway —
    so repaint that tone instead of trusting the model."""
    crop = _panel()
    filled = crop.copy()
    filled[10:30, 10:40] = 5                # LaMa's black blob
    out = _apply_inpaint(crop, filled, [{"x": 10, "y": 10, "w": 30, "h": 20}])
    assert out[20, 25].mean() > 180, "a black blob must not survive"
    assert out[20, 25].mean() < 256


def test_degenerate_boxes_are_skipped():
    crop = _panel()
    filled = np.zeros_like(crop)
    for box in ({"x": 0, "y": 0, "w": 0, "h": 0}, {"x": 79, "y": 79, "w": 5, "h": 5},
                {"x": -50, "y": -50, "w": 10, "h": 10}):
        out = _apply_inpaint(crop, filled, [box])
        assert out.shape == crop.shape


@pytest.mark.parametrize("bg", [30, 200])
def test_works_on_dark_and_light_art(bg):
    crop = _panel(bg)
    filled = crop.copy()
    filled[10:30, 10:40] = 252
    out = _apply_inpaint(crop, filled, [{"x": 10, "y": 10, "w": 30, "h": 20}])
    assert out[20, 25].mean() > 200


# ─── the guard must not fire on LIGHT lettering ──────────────────────────────
# A first version keyed the expected fill on "the brightest quarter of the pixels", which is
# right for black ink on a white bubble and exactly backwards for white lettering on dark art:
# there the bright quarter IS the lettering, so a CORRECT dark fill was judged implausible and
# the region got repainted bright — worse than the bug the guard was added to stop.

def _lettering(bg: int, ink: int, text: str = "BOOM", scale: float = 0.9, thick: int = 2):
    import cv2
    img = np.full((70, 120, 3), bg, np.uint8)
    cv2.putText(img, text, (10, 45), cv2.FONT_HERSHEY_DUPLEX, scale, (ink,) * 3, thick,
                cv2.LINE_AA)
    return img


@pytest.mark.parametrize("text,scale,thick", [("BOOM", 0.6, 2), ("CRASH", 0.9, 2),
                                              ("WHAM", 1.3, 3), ("catch", 0.7, 1)])
def test_light_lettering_on_dark_art_keeps_the_model_fill(text, scale, thick):
    """LaMa reconstructing the dark artwork is CORRECT here — the guard must stand aside."""
    crop = _lettering(40, 250, text, scale, thick)
    filled = crop.copy()
    filled[10:60, 8:112] = 40                       # the right answer: the art behind it
    out = _apply_inpaint(crop, filled, [{"x": 8, "y": 10, "w": 104, "h": 50}])
    assert abs(float(out[35, 60].mean()) - 40) < 15


def test_dark_lettering_on_a_paper_bubble_still_rejects_a_black_fill():
    """The original defect, kept under test: paper dominates, so a near-black fill is a
    hallucination no matter how confident the model is."""
    crop = _lettering(255, 20)
    filled = crop.copy()
    filled[10:60, 8:112] = 5
    out = _apply_inpaint(crop, filled, [{"x": 8, "y": 10, "w": 104, "h": 50}])
    assert out[35, 60].mean() > 180
