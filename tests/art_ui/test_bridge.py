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
