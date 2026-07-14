"""Gutter-split: an oversized whole-page Magi box is re-split along white gutters;
normal-size boxes and true splashes are untouched. Pure numpy, no Magi needed."""
import numpy as np

from stages.stage_2.gutter_split import _gutter_split


def _grid_2x2(size=400):
    """White canvas with four dark cells separated by wide white gutters."""
    img = np.full((size, size), 255, dtype=np.uint8)
    for yy in (slice(20, 180), slice(220, 380)):
        for xx in (slice(20, 180), slice(220, 380)):
            img[yy, xx] = 30
    return img


def test_splits_2x2_grid_into_four():
    img = _grid_2x2()
    subs = _gutter_split(img, {"x": 0, "y": 0, "w": 400, "h": 400}, 400, 400)
    assert len(subs) == 4
    # reading order: top-left, top-right, bottom-left, bottom-right
    xs = [s["x"] for s in subs]
    ys = [s["y"] for s in subs]
    assert ys[0] == ys[1] < ys[2] == ys[3]
    assert xs[0] < xs[1] and xs[2] < xs[3]


def test_solid_splash_is_noop():
    img = np.full((400, 400), 120, dtype=np.uint8)  # dense art, no white gutters
    subs = _gutter_split(img, {"x": 0, "y": 0, "w": 400, "h": 400}, 400, 400)
    assert len(subs) == 1
    assert subs[0] == {"x": 0, "y": 0, "w": 400, "h": 400}


def test_small_panel_below_threshold_is_noop():
    img = _grid_2x2()  # has gutters, but the box is too small to be a candidate
    bbox = {"x": 0, "y": 0, "w": 100, "h": 100}  # 0.0625 of page < 0.55 threshold
    subs = _gutter_split(img, bbox, 400, 400)
    assert subs == [bbox]


def test_rgb_image_accepted():
    img = np.stack([_grid_2x2()] * 3, axis=-1)  # HxWx3 RGB
    subs = _gutter_split(img, {"x": 0, "y": 0, "w": 400, "h": 400}, 400, 400)
    assert len(subs) == 4
