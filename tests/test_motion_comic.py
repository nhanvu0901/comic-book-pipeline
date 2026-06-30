"""Motion-comic (Approach A): action/impact panels get a stronger, faster camera
push; calm panels keep the prior subtle Ken Burns byte-for-byte."""
from stages.stage_5.shots import _is_action_text, _zoompan_expr, _choose_motion


def _panel(w, h):
    return {"bbox": {"x": 0, "y": 0, "w": w, "h": h}}


def test_choose_motion_never_static():
    # Regression: half this project's shots were motion="static" (z=1.00 held)
    # and visibly FROZE. _choose_motion must ALWAYS return a moving motion now —
    # across panel sizes, durations, missing bbox, and None.
    cases = [
        _panel(1100, 1700),   # splash
        _panel(300, 200),     # tiny panel (used to be static)
        _panel(0, 0),         # degenerate bbox (used to be static)
        {},                   # no bbox
        None,                 # no panel
    ]
    for seq in range(6):
        for p in cases:
            for dur in (0.5, 1.4, 3.0, 7.0):   # short durations used to force static
                assert _choose_motion(p, dur, seq=seq) != "static"


def test_choose_motion_splash_zooms_in():
    assert _choose_motion(_panel(1100, 1700), 3.0) == "zoom_in"   # splash → epic zoom_in


def test_choose_motion_cold_open_always_zoom_in():
    # The first shot (seq 0) is the epic cold open — always zoom_in, any panel shape.
    for p in (_panel(300, 600), _panel(600, 300), _panel(300, 300), _panel(1100, 1700)):
        assert _choose_motion(p, 3.0, seq=0) == "zoom_in"


def test_choose_motion_aspect_aware_pans():
    # After the cold open (seq>0) motions ROTATE through the moves that read on the
    # panel's shape: tall → vertical pans available, wide → horizontal, square → all.
    tall = _panel(300, 600)        # ar 2.0 → [zoom_in, zoom_out, pan_down, pan_up]
    assert _choose_motion(tall, 3.0, seq=2) == "pan_down"
    assert _choose_motion(tall, 3.0, seq=3) == "pan_up"
    assert "pan_right" not in {_choose_motion(tall, 3.0, seq=s) for s in range(1, 6)}
    wide = _panel(600, 300)        # ar 0.5 → [zoom_in, zoom_out, pan_right]
    assert _choose_motion(wide, 3.0, seq=2) == "pan_right"
    assert {_choose_motion(wide, 3.0, seq=s) for s in range(1, 6)}.isdisjoint(
        {"pan_down", "pan_up"})
    sq = _panel(300, 300)          # ar 1.0 → all five rotate
    got = [_choose_motion(sq, 3.0, seq=i) for i in range(1, 6)]
    assert got == ["zoom_out", "pan_down", "pan_up", "pan_right", "zoom_in"]


def test_choose_motion_big_panels_not_all_zoom_in():
    # Regression (Master 2026-06-28): a run of big splashes used to collapse to 15/17
    # identical zoom_in. Now seq>0 splashes rotate → ≥3 distinct motions incl a vertical pan.
    splash = _panel(1100, 1700)    # big AND tall (ar ~1.5)
    motions = {_choose_motion(splash, 3.0, seq=s) for s in range(1, 6)}
    assert len(motions) >= 3
    assert motions & {"pan_down", "pan_up"}


def test_action_text_detects_impact_verbs():
    assert _is_action_text("Galactus consumed it, and monstrous dragons tore from the core and attacked him.")
    assert _is_action_text("Iron Man's repulsor blast slammed into the Hulk.")
    assert _is_action_text("She unleashed the Power Cosmic.")


def test_action_text_false_on_calm_clause():
    assert not _is_action_text("She accepted the burden and wept alone, knowing every future was gone.")
    assert not _is_action_text("At his citadel on the moon, the Watcher confronted Rogue with a parable.")
    assert not _is_action_text("")


def test_calm_zoom_in_is_unchanged():
    # action=False must reproduce the prior subtle expression exactly (no regression).
    out = _zoompan_expr("zoom_in", 60, action=False)
    assert "z='1+0.05*pow(on/60,2)*(3-2*(on/60))'" in out


def test_action_zoom_in_is_stronger_and_faster():
    out = _zoompan_expr("zoom_in", 60, action=True)
    assert "1+0.13*" in out          # bigger push (0.13 vs 0.05)
    assert "(1-pow(1-on/60,2))" in out   # ease-OUT punch, not smoothstep


def test_action_zoom_out_and_pan_scale_up():
    assert "1.13-0.13*" in _zoompan_expr("zoom_out", 60, action=True)
    assert "iw*0.06" in _zoompan_expr("pan_right", 60, action=True)
    # calm pan unchanged
    assert "iw*0.03" in _zoompan_expr("pan_right", 60, action=False)
