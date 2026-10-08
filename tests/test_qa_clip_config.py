import os
import importlib
import pytest


@pytest.fixture(autouse=True)
def _config_without_dotenv_leakage(monkeypatch):
    """config.py calls load_dotenv() on every (re)import, so a developer's or the server's
    .env (POST_ATEMPO=1.15 in production) leaked into the "code default" assertions and
    made them fail for reasons that are not regressions. Reload config with .env disabled,
    then put the real config back for the tests that run after this file."""
    import dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)
    yield
    monkeypatch.undo()
    import config
    importlib.reload(config)


def test_default_flags_off(monkeypatch):
    monkeypatch.delenv("ENABLE_VIDEO_CLIPS", raising=False)
    monkeypatch.delenv("CLIP_SPEED_MIN", raising=False)
    monkeypatch.delenv("CLIP_SPEED_MAX", raising=False)
    monkeypatch.delenv("CLIP_MAX_HOLD", raising=False)
    monkeypatch.delenv("CHATTERBOX_SEED", raising=False)
    monkeypatch.delenv("POST_ATEMPO", raising=False)

    import config
    importlib.reload(config)

    assert config.ENABLE_VIDEO_CLIPS is False
    assert config.CLIP_SPEED_MIN == 0.8
    assert config.CLIP_SPEED_MAX == 1.25
    assert config.CLIP_MAX_HOLD == 0.3
    assert config.POST_ATEMPO == 1.30  # default unchanged
    assert config.CHATTERBOX_SEED is None  # None when flag is OFF


def test_custom_env_flags(monkeypatch):
    monkeypatch.setenv("ENABLE_VIDEO_CLIPS", "1")
    monkeypatch.setenv("CLIP_SPEED_MIN", "0.75")
    monkeypatch.setenv("CLIP_SPEED_MAX", "1.30")
    monkeypatch.setenv("CLIP_MAX_HOLD", "0.4")
    monkeypatch.setenv("CHATTERBOX_SEED", "123")
    monkeypatch.setenv("POST_ATEMPO", "1.15")

    import config
    importlib.reload(config)

    assert config.ENABLE_VIDEO_CLIPS is True
    assert config.CLIP_SPEED_MIN == 0.75
    assert config.CLIP_SPEED_MAX == 1.30
    assert config.CLIP_MAX_HOLD == 0.4
    assert config.POST_ATEMPO == 1.15
    assert config.CHATTERBOX_SEED == 123


def test_chatterbox_seed_default_when_flag_on(monkeypatch):
    monkeypatch.setenv("ENABLE_VIDEO_CLIPS", "1")
    monkeypatch.delenv("CHATTERBOX_SEED", raising=False)

    import config
    importlib.reload(config)

    assert config.ENABLE_VIDEO_CLIPS is True
    assert config.CHATTERBOX_SEED == 42


def test_chatterbox_seed_none_when_flag_off_even_if_env_set(monkeypatch):
    monkeypatch.setenv("ENABLE_VIDEO_CLIPS", "0")
    monkeypatch.setenv("CHATTERBOX_SEED", "999")

    import config
    importlib.reload(config)

    assert config.ENABLE_VIDEO_CLIPS is False
    assert config.CHATTERBOX_SEED is None
