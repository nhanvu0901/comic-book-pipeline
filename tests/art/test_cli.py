"""Tests for art_pipeline.cli — --length flag, outline command, longform dispatch."""
import json
import pytest

from art_pipeline import fetch, cli


# ─── helpers shared across tests ────────────────────────────────────────────

_GOOD_META = {
    "objectID": 1, "isPublicDomain": True, "primaryImage": "http://x/i.jpg",
    "title": "T", "artistDisplayName": "A", "objectDate": "1880",
    "department": "D", "creditLine": "C", "objectURL": "http://met/1",
    "medium": "Oil",
}


def _patch_met(monkeypatch, tmp_path, project="proj"):
    """Patch Met API + project path so fetch_artworks doesn't hit the network."""
    monkeypatch.setattr(fetch.met, "fetch_meta", lambda oid: _GOOD_META)
    monkeypatch.setattr(fetch.met, "fetch_image",
                        lambda m, dest: (dest.write_bytes(b"fakejpg"), dest)[1])
    monkeypatch.setattr(fetch, "get_art_project_path", lambda n: tmp_path / n)
    (tmp_path / project).mkdir(exist_ok=True)


# ─── 1. selection.json records length ────────────────────────────────────────

def test_selection_records_length(tmp_path, monkeypatch):
    _patch_met(monkeypatch, tmp_path)
    fetch.fetch_artworks("proj", [1], mode="painting_story", length="longform",
                         log=lambda m: None)
    sel = json.loads((tmp_path / "proj" / "selection.json").read_text())
    assert sel["length"] == "longform"
    assert sel["mode"] == "painting_story"


def test_selection_records_short_by_default(tmp_path, monkeypatch):
    _patch_met(monkeypatch, tmp_path)
    fetch.fetch_artworks("proj", [1], log=lambda m: None)
    sel = json.loads((tmp_path / "proj" / "selection.json").read_text())
    assert sel["length"] == "short"


# ─── 2. validation errors ─────────────────────────────────────────────────────

def test_fetch_rejects_bad_length(tmp_path, monkeypatch):
    _patch_met(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="'short' or 'longform'"):
        fetch.fetch_artworks("proj", [1], length="weird", log=lambda m: None)


def test_fetch_rejects_longform_with_short_mode(tmp_path, monkeypatch):
    _patch_met(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="length='longform' requires mode"):
        fetch.fetch_artworks("proj", [1], mode="painting_deep_dive",
                             length="longform", log=lambda m: None)


# ─── 3. narrate dispatches to longform when selection says longform ───────────

def _make_project(tmp_path, length="longform"):
    """Create a minimal project dir with selection.json."""
    proj_dir = tmp_path / "myproj"
    proj_dir.mkdir()
    (proj_dir / "selection.json").write_text(
        json.dumps({"mode": "painting_story", "length": length}))
    return proj_dir


def test_cli_narrate_dispatches_longform(tmp_path, monkeypatch):
    proj_dir = _make_project(tmp_path, length="longform")

    # Make get_art_project_path return our tmp dir
    import art_pipeline.cli as cli_mod
    monkeypatch.setattr("art_pipeline.config.get_art_project_path",
                        lambda n: proj_dir)

    outline_calls = []
    narrate_lf_calls = []

    import art_pipeline.outline as outline_mod
    import art_pipeline.narrate_longform as nlf_mod
    monkeypatch.setattr(outline_mod, "write_outline",
                        lambda proj, mode=None, force=False: outline_calls.append(proj))
    monkeypatch.setattr(nlf_mod, "write_longform_narration",
                        lambda proj: narrate_lf_calls.append(proj))

    result = cli.main(["narrate", "myproj"])
    assert result == 0
    assert outline_calls == ["myproj"]
    assert narrate_lf_calls == ["myproj"]


# ─── 4. narrate falls back to short when no length key (legacy) ──────────────

def test_cli_narrate_short_unchanged(tmp_path, monkeypatch):
    proj_dir = tmp_path / "myproj"
    proj_dir.mkdir()
    # Legacy selection.json — no "length" key
    (proj_dir / "selection.json").write_text(
        json.dumps({"mode": "painting_deep_dive"}))

    monkeypatch.setattr("art_pipeline.config.get_art_project_path",
                        lambda n: proj_dir)

    short_calls = []
    import art_pipeline.narrate as narrate_mod
    monkeypatch.setattr(narrate_mod, "write_narration",
                        lambda proj, mode=None: short_calls.append(proj))

    result = cli.main(["narrate", "myproj"])
    assert result == 0
    assert short_calls == ["myproj"]


# ─── 5. tts dispatches to synthesize_longform ────────────────────────────────

def test_cli_tts_dispatches_longform(tmp_path, monkeypatch):
    proj_dir = _make_project(tmp_path, length="longform")
    monkeypatch.setattr("art_pipeline.config.get_art_project_path",
                        lambda n: proj_dir)

    lf_tts_calls = []
    import art_pipeline.longform_tts as lftts_mod
    monkeypatch.setattr(lftts_mod, "synthesize_longform",
                        lambda proj, force=False, calm=True: lf_tts_calls.append(proj))

    result = cli.main(["tts", "myproj"])
    assert result == 0
    assert lf_tts_calls == ["myproj"]


# ─── 6. tts uses short path when length=short ────────────────────────────────

def test_cli_tts_short_unchanged(tmp_path, monkeypatch):
    proj_dir = _make_project(tmp_path, length="short")
    monkeypatch.setattr("art_pipeline.config.get_art_project_path",
                        lambda n: proj_dir)

    short_tts_calls = []
    import art_pipeline.tts as tts_mod
    monkeypatch.setattr(tts_mod, "synthesize_art",
                        lambda proj, force=False, calm=True: short_tts_calls.append(proj))

    result = cli.main(["tts", "myproj"])
    assert result == 0
    assert short_tts_calls == ["myproj"]


# ─── 7. standalone outline command ───────────────────────────────────────────

def test_cli_outline_command(tmp_path, monkeypatch):
    proj_dir = _make_project(tmp_path, length="longform")
    monkeypatch.setattr("art_pipeline.config.get_art_project_path",
                        lambda n: proj_dir)

    outline_calls = []
    import art_pipeline.outline as outline_mod
    monkeypatch.setattr(outline_mod, "write_outline",
                        lambda proj, mode=None, force=False: outline_calls.append((proj, mode, force)))

    result = cli.main(["outline", "myproj", "--force"])
    assert result == 0
    assert outline_calls == [("myproj", None, True)]
