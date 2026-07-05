def test_xfade_config_defaults():
    import config
    assert isinstance(config.XFADE_DURATION, float)
    assert config.XFADE_DURATION == 0.25
    # Default flipped to hard-cut (competitor autopsy: 0.4-0.8 cuts/s, near-zero
    # dissolves); "dissolve" is now an explicit opt-in via XFADE_TRANSITION=dissolve.
    assert config.XFADE_TRANSITION == "dissolve"  # Master 2026-07-05: old pacing default; "cut" = competitor mode
    assert config.XFADE_SOFT_EDGES is True
