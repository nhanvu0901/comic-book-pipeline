import json
from art_ui import bridge


def test_print_to_redirects_and_restores(capsys):
    lines = []
    with bridge._print_to(lines.append):
        print("captured", 123)
    print("normal")
    assert lines == ["captured 123"]
    assert "normal" in capsys.readouterr().out


def test_loaders_return_empty_on_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "ART_ROOT", tmp_path)
    assert bridge.load_art_pages("nope") == []
    assert bridge.load_art_context("nope") is None
    assert bridge.load_art_narration("nope") is None


def test_save_narration_edits_recomputes_word_count(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "ART_ROOT", tmp_path)
    (tmp_path / "p").mkdir()
    narration = {"scenes": [{"scene_id": 1, "text": "one two three four", "word_count": 99}]}
    bridge.save_narration_edits("p", narration)
    saved = json.loads((tmp_path / "p" / "narration.json").read_text())
    assert saved["scenes"][0]["word_count"] == 4


def test_run_hunt_delegates(monkeypatch):
    from art_ui import bridge
    calls = {}

    def _mock_hunt(project, force=None, log=None):
        calls["args"] = (project, force)
        return {"resolved": 1}

    monkeypatch.setattr("art_pipeline.hunt.hunt_visuals", _mock_hunt)
    out = bridge.run_hunt("proj", True, lambda m: None)
    assert out == {"resolved": 1}
    assert calls["args"] == ("proj", True)


# ── _project_length ────────────────────────────────────────────────────────

def test_project_length_reads_selection_json(tmp_path, monkeypatch):
    from art_ui import bridge
    monkeypatch.setattr(bridge, "ART_ROOT", tmp_path)
    proj = tmp_path / "p"; proj.mkdir()
    (proj / "selection.json").write_text('{"length": "longform"}')
    assert bridge._project_length("p") == "longform"


def test_project_length_defaults_short_when_missing(tmp_path, monkeypatch):
    from art_ui import bridge
    monkeypatch.setattr(bridge, "ART_ROOT", tmp_path)
    (tmp_path / "p").mkdir()
    assert bridge._project_length("p") == "short"


# ── run_narrate dispatch ────────────────────────────────────────────────────

def test_bridge_narrate_longform_dispatch(monkeypatch, tmp_path):
    from art_ui import bridge
    monkeypatch.setattr(bridge, "ART_ROOT", tmp_path)
    proj = tmp_path / "p"; proj.mkdir()
    (proj / "selection.json").write_text('{"length": "longform"}')
    calls = []
    monkeypatch.setattr("art_pipeline.outline.write_outline",
                        lambda *a, **k: calls.append("outline") or {})
    monkeypatch.setattr("art_pipeline.narrate_longform.write_longform_narration",
                        lambda *a, **k: calls.append("lf") or {})
    bridge.run_narrate("p", None, lambda *_: None)
    assert calls == ["outline", "lf"]


def test_bridge_narrate_legacy_dispatch(monkeypatch, tmp_path):
    """No selection.json → short path → write_narration called, not longform."""
    from art_ui import bridge
    monkeypatch.setattr(bridge, "ART_ROOT", tmp_path)
    (tmp_path / "p").mkdir()
    calls = []
    monkeypatch.setattr("art_pipeline.narrate.write_narration",
                        lambda *a, **k: calls.append("narrate") or {})
    bridge.run_narrate("p", None, lambda *_: None)
    assert calls == ["narrate"]


# ── run_tts dispatch ────────────────────────────────────────────────────────

def test_bridge_tts_longform_dispatch(monkeypatch, tmp_path):
    from art_ui import bridge
    monkeypatch.setattr(bridge, "ART_ROOT", tmp_path)
    proj = tmp_path / "p"; proj.mkdir()
    (proj / "selection.json").write_text('{"length": "longform"}')
    calls = []
    monkeypatch.setattr("art_pipeline.longform_tts.synthesize_longform",
                        lambda *a, **k: calls.append("lf_tts") or {})
    bridge.run_tts("p", lambda *_: None)
    assert calls == ["lf_tts"]


def test_bridge_tts_legacy_dispatch(monkeypatch, tmp_path):
    """No selection.json → short path → synthesize_art called."""
    from art_ui import bridge
    monkeypatch.setattr(bridge, "ART_ROOT", tmp_path)
    (tmp_path / "p").mkdir()
    calls = []

    class _FakeResult:
        def to_dict(self):
            calls.append("tts")
            return {}

    monkeypatch.setattr("art_pipeline.tts.synthesize_art",
                        lambda *a, **k: _FakeResult())
    bridge.run_tts("p", lambda *_: None)
    assert calls == ["tts"]
