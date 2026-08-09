"""Magi character boxes drive the 9:16 cover-crop window (2026-08-09).

Stage 2 always computed these boxes and threw them away, so Stage 5 had to infer the
subject from ink density — which cannot tell a person from lettering, and framed big
SFX instead of the face. These tests pin the three things that fix depends on:
the profile shape, the page→crop coordinate trip, and the fallback staying intact.
"""
import numpy as np
import pytest

from stages.stage_5.shots import (
    _char_box_profile,
    _choose_crop_offset,
    _to_crop_local,
)


def _mass_in(profile, lo_frac, hi_frac):
    """Share of a profile's mass inside a fractional span — mirrors _choose_crop_offset."""
    n = len(profile)
    return float(profile[int(lo_frac * n):int(hi_frac * n)].sum() / profile.sum())


# ── _char_box_profile ────────────────────────────────────────────────────────

def test_no_boxes_returns_none_so_caller_falls_back():
    assert _char_box_profile(100, 100, []) == (None, None)
    assert _char_box_profile(100, 100, None) == (None, None)


def test_zero_area_box_is_ignored():
    assert _char_box_profile(100, 100, [{"x": 5, "y": 5, "w": 0, "h": 10}]) == (None, None)


def test_box_outside_panel_is_clipped_away():
    assert _char_box_profile(100, 100, [{"x": 200, "y": 200, "w": 50, "h": 50}]) == (None, None)


def test_mass_sits_where_the_figure_is():
    # One figure on the RIGHT third of a 300px-wide panel.
    cols, rows = _char_box_profile(300, 200, [{"x": 200, "y": 20, "w": 80, "h": 160}])
    assert _mass_in(cols, 0.66, 1.0) > 0.95
    assert _mass_in(cols, 0.0, 0.5) == 0.0


def test_figure_outweighs_a_lone_hand_box():
    """Magi emits a box for a bare hand/fist as well as a whole figure (seen on
    Absolute Batman p8/p10). Coverage-weighted mass must let the figure win, so no
    'largest box' special case is needed."""
    hand = {"x": 10, "y": 90, "w": 30, "h": 30}          # 900 px
    figure = {"x": 200, "y": 20, "w": 80, "h": 160}      # 12800 px
    cols, _ = _char_box_profile(300, 200, [hand, figure])
    assert _mass_in(cols, 0.0, 0.2) < 0.15               # hand side
    assert _mass_in(cols, 0.6, 1.0) > 0.80               # figure side


def test_head_band_is_weighted_above_the_body():
    """A head sits at the top of an upright figure; the top slice carries CHAR_HEAD_W."""
    _, rows = _char_box_profile(100, 200, [{"x": 10, "y": 0, "w": 50, "h": 200}])
    top = rows[:70].sum()      # ~ the 35% head band
    bottom = rows[130:].sum()  # an equal-height slice of body
    assert top > bottom * 1.5


def test_profiles_are_normalized_to_one():
    cols, rows = _char_box_profile(120, 90, [{"x": 10, "y": 10, "w": 40, "h": 40}])
    assert cols.max() == pytest.approx(1.0)
    assert rows.max() == pytest.approx(1.0)


# ── _to_crop_local ───────────────────────────────────────────────────────────

_GEOM = {"left": 100, "top": 50, "right": 400, "bottom": 250, "mirrored": False}


def test_page_box_becomes_crop_relative():
    out = _to_crop_local([{"x": 150, "y": 80, "w": 60, "h": 40}], _GEOM)
    assert out == [{"x": 50, "y": 30, "w": 60, "h": 40}]


def test_box_is_clipped_to_the_crop():
    out = _to_crop_local([{"x": 350, "y": 200, "w": 200, "h": 200}], _GEOM)
    assert out == [{"x": 250, "y": 150, "w": 50, "h": 50}]


def test_box_entirely_outside_the_crop_drops():
    assert _to_crop_local([{"x": 500, "y": 500, "w": 20, "h": 20}], _GEOM) == []


def test_mirrored_crop_reflects_x():
    """The crop was flipped but the page coords were not — miss this and every box
    lands on the mirror image of its subject."""
    geom = dict(_GEOM, mirrored=True)
    out = _to_crop_local([{"x": 150, "y": 80, "w": 60, "h": 40}], geom)
    # crop width 300, box occupies local x 50..110 → mirrored to 190..250
    assert out == [{"x": 190, "y": 30, "w": 60, "h": 40}]


def test_empty_input_is_empty_output():
    assert _to_crop_local(None, _GEOM) == []
    assert _to_crop_local([], _GEOM) == []


# ── the actual bug: SFX must not win the frame ───────────────────────────────

def test_window_follows_the_character_not_the_sfx_side():
    """The Absolute Batman p12 shape: figure on one side, giant 'BLAM' on the other.
    Ink density reads both as subject; character boxes read only the figure."""
    panel_w, panel_h = 2000, 1920          # wide enough that the 1080 window can slide
    cols, rows = _char_box_profile(panel_w, panel_h,
                                   [{"x": 1500, "y": 200, "w": 400, "h": 1400}])
    x0, _y0 = _choose_crop_offset(panel_w, panel_h, 1080, 1920, [],
                                  detail_cols=cols, detail_rows=rows)
    # The figure spans 1500-1900; the window must cover it, not the empty/SFX left half.
    assert x0 + 1080 > 1500, f"window {x0}..{x0 + 1080} misses the figure"
    assert x0 > (panel_w - 1080) // 2, "window did not leave dead center toward the figure"


def test_centered_subject_keeps_dead_center():
    """Hysteresis: a figure already centered must not be nudged (no needless reframing)."""
    panel_w, panel_h = 2000, 1920
    cols, rows = _char_box_profile(panel_w, panel_h,
                                   [{"x": 800, "y": 200, "w": 400, "h": 1400}])
    x0, _ = _choose_crop_offset(panel_w, panel_h, 1080, 1920, [],
                                detail_cols=cols, detail_rows=rows)
    assert x0 == (panel_w - 1080) // 2


def test_no_profile_is_byte_identical_to_old_center_crop():
    """Old projects carry no character boxes — the window must not move."""
    x0, y0 = _choose_crop_offset(2000, 1920, 1080, 1920, [],
                                 detail_cols=None, detail_rows=None)
    assert (x0, y0) == ((2000 - 1080) // 2, 0)


def test_profile_length_matches_panel_axes():
    """_choose_crop_offset maps profiles by POSITION FRACTION, so a full-resolution
    profile and the old thumbnail profile are interchangeable — guard the shape."""
    cols, rows = _char_box_profile(640, 480, [{"x": 100, "y": 100, "w": 200, "h": 200}])
    assert isinstance(cols, np.ndarray) and len(cols) == 640
    assert len(rows) == 480
