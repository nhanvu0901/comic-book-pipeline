def test_xfade_config_defaults():
    import config
    assert isinstance(config.XFADE_DURATION, float)
    assert config.XFADE_DURATION == 0.25
    assert config.XFADE_TRANSITION == "dissolve"
