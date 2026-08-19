"""Per-project music genre, chosen in the Review Beats UI.

The genre is a per-PROJECT decision (a reaction to that story), not a global setting, so it
lives in projects/<slug>/music.json. A project that never chose one must fall back to
config.MUSIC_GENRE rather than render silently wrong — every project shipped before this
existed is in exactly that state.
"""
import json

import config
from ui.bridge import load_music_config, save_music_config


def test_missing_file_reads_as_unset(tmp_path, monkeypatch):
    """Every existing project is in this state — it must not raise, and must not invent
    a genre of its own."""
    monkeypatch.setattr("ui.bridge.PROJECTS_ROOT", tmp_path)
    (tmp_path / "proj").mkdir()
    assert load_music_config("proj") == {}


def test_unset_falls_back_to_the_configured_default(tmp_path, monkeypatch):
    monkeypatch.setattr("ui.bridge.PROJECTS_ROOT", tmp_path)
    (tmp_path / "proj").mkdir()
    doc = load_music_config("proj")
    assert (doc.get("genre") or config.MUSIC_GENRE) == "minimal dark cinematic"


def test_roundtrip_and_file_shape(tmp_path, monkeypatch):
    monkeypatch.setattr("ui.bridge.PROJECTS_ROOT", tmp_path)
    (tmp_path / "proj").mkdir()
    save_music_config("proj", {"genre": "minimal horror piano"})

    on_disk = json.loads((tmp_path / "proj" / "music.json").read_text())
    assert on_disk == {"genre": "minimal horror piano"}
    assert load_music_config("proj")["genre"] == "minimal horror piano"


def test_a_custom_genre_is_kept_verbatim(tmp_path, monkeypatch):
    """The dropdown is editable on purpose: the preset list is a starting point, not the
    vocabulary. A typed style brief must survive untouched — it is prompt text."""
    monkeypatch.setattr("ui.bridge.PROJECTS_ROOT", tmp_path)
    (tmp_path / "proj").mkdir()
    custom = "sparse detuned piano, long silences, one low hit per phrase"
    save_music_config("proj", {"genre": custom})
    assert load_music_config("proj")["genre"] == custom


def test_corrupt_file_does_not_crash_the_review_screen(tmp_path, monkeypatch):
    monkeypatch.setattr("ui.bridge.PROJECTS_ROOT", tmp_path)
    (tmp_path / "proj").mkdir()
    (tmp_path / "proj" / "music.json").write_text("{not json")
    assert load_music_config("proj") == {}


def test_default_is_a_sparse_style_and_presets_contain_it():
    """Not a style preference: six DENSE beds were measured inaudible under this narration
    while a sparse one came through, so the default has to be one that leaves gaps."""
    assert config.MUSIC_GENRE == "minimal dark cinematic"
    assert config.MUSIC_GENRE in config.MUSIC_GENRES
    assert len(config.MUSIC_GENRES) >= 2


def test_music_generation_targets_the_private_minimax_space_without_legacy_runtime_config():
    """The production scorer is remote MiniMax; its old local runtime is not configured."""
    assert config.HF_MUSIC_SPACE == "Neopet2001/MiniMax-Music3"
    assert config.HF_MUSIC_STEPS == 30
    assert config.HF_MUSIC_GUIDANCE == 1.7
    assert not hasattr(config, "ACE" + "STEP_VENV")
