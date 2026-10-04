import config


def test_topup_rounds_parser_defaults_and_clamps(monkeypatch):
    monkeypatch.delenv("SCOUT_TOPUP_ROUNDS", raising=False)
    assert config._bounded_int_env("SCOUT_TOPUP_ROUNDS", 1, 0, 1) == 1

    monkeypatch.setenv("SCOUT_TOPUP_ROUNDS", "0")
    assert config._bounded_int_env("SCOUT_TOPUP_ROUNDS", 1, 0, 1) == 0
    monkeypatch.setenv("SCOUT_TOPUP_ROUNDS", "99")
    assert config._bounded_int_env("SCOUT_TOPUP_ROUNDS", 1, 0, 1) == 1
    monkeypatch.setenv("SCOUT_TOPUP_ROUNDS", "-7")
    assert config._bounded_int_env("SCOUT_TOPUP_ROUNDS", 1, 0, 1) == 0
    monkeypatch.setenv("SCOUT_TOPUP_ROUNDS", "invalid")
    assert config._bounded_int_env("SCOUT_TOPUP_ROUNDS", 1, 0, 1) == 1
