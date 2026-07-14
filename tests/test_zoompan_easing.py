from stages.stage_5.shots import _zoompan_expr


def test_zoom_in_is_eased_not_linear():
    e = _zoompan_expr("zoom_in", 30)
    # smoothstep present, anchored to the frame count, and NOT the old linear "zoom+" ramp.
    # Amplitude 0.06 (old-look 0.05 raised to the freeze-floor; Master 2026-07-05 kept old pacing).
    assert "pow(on/30,2)" in e
    assert "(3-2*(on/30))" in e
    assert "1+0.06*" in e
    assert "zoom+" not in e


def test_zoom_out_eased_endpoints():
    e = _zoompan_expr("zoom_out", 30)
    assert "1.06-0.06*" in e   # 1.06 → 1.00 (ends at z=1.0, loop framing)
    assert "pow(on/30,2)" in e


def test_pan_right_linear_full_travel():
    # Master 2026-07-11: pan sweeps the full excess (0 → iw-iw/zoom) LINEARLY, z held at
    # PAN_ZOOM (1.15). No ease — constant velocity, lands on the far edge at the last frame.
    e = _zoompan_expr("pan_right", 30)
    assert "x='(iw-iw/zoom)*(on/30)'" in e
    assert "z='1.15'" in e
    assert "pow(" not in e   # linear, not smoothstep


def test_static_unchanged():
    e = _zoompan_expr("static", 30)
    assert "z='1.00'" in e
    assert "pow(" not in e
