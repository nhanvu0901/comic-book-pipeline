"""Tests for stages/stage_3/screen_qa.py (Task 4)."""
import json
from pathlib import Path
import pytest

from stages.stage_3 import screen_qa as sq
from stages.stage_3.schema import Narration, Scene
from stages.stage_5.shots import _vb_text


def test_visual_beat_dict_contract():
    # Verify the interface contract: visual_beat dict {text, query}
    # and compatibility with stages.stage_5.shots._vb_text
    vb = {"text": "Scott Lang escapes the Quantum Realm,", "query": "Avengers Endgame Scott Lang van"}
    assert _vb_text(vb) == "Scott Lang escapes the Quantum Realm,"


def test_normalize_visual_beats():
    raw_vbs = [
        {"text": "Tony Stark tests the GPS,", "query": "Avengers Endgame Tony Stark GPS"},
        "and discovers the model works."  # raw string
    ]
    normalized = sq._normalize_visual_beats(raw_vbs, default_query="Avengers Endgame Tony Stark")
    assert len(normalized) == 2
    assert normalized[0] == {"text": "Tony Stark tests the GPS,", "query": "Avengers Endgame Tony Stark GPS"}
    assert normalized[1] == {"text": "and discovers the model works.", "query": "Avengers Endgame Tony Stark"}


def test_write_screen_qa_mock(tmp_path, monkeypatch):
    proj = "test_endgame_qa"
    proj_root = tmp_path / proj
    proj_root.mkdir(parents=True)

    screen_ctx = {
        "question": "How did the Avengers travel back in time in Endgame?",
        "items": [
            {
                "entity": "Scott Lang",
                "event": "Introduces Quantum Realm concept",
                "adaptation_title": "Avengers: Endgame",
                "year": 2019,
                "summary": "Scott Lang returns from the Quantum Realm and proposes a time heist.",
                "visual_query": "Avengers Endgame Scott Lang van quantum realm",
                "source_urls": ["https://marvelcinematicuniverse.fandom.com/wiki/Time_Heist"],
            },
            {
                "entity": "Tony Stark",
                "event": "Builds the Time-Space GPS",
                "adaptation_title": "Avengers: Endgame",
                "year": 2019,
                "summary": "Tony models the inverted Mobius strip to navigate time safely.",
                "visual_query": "Avengers Endgame Tony Stark discovers GPS",
                "source_urls": ["https://marvelcinematicuniverse.fandom.com/wiki/Time-Space_GPS"],
            }
        ]
    }
    (proj_root / "screen_context.json").write_text(json.dumps(screen_ctx))

    monkeypatch.setattr("stages.stage_3.screen_qa.get_project_dirs",
                        lambda p: {"root": proj_root, "preprocessed": proj_root / "preprocessed"})

    fake_llm_json = json.dumps({
        "title": "How The Avengers Conquered Time Travel",
        "hook": "Traveling back in time seemed impossible until Scott Lang returned from the Quantum Realm.",
        "outro": "By uniting Pym Particles and Tony's GPS, the Avengers made time travel a reality.",
        "scenes": [
            {
                "text": "In Avengers: Endgame (2019), Scott Lang emerges from the Quantum Realm with proof that time moves differently inside it.",
                "visual_beats": [
                    {"text": "In Avengers: Endgame (2019),", "query": "Avengers Endgame title card"},
                    {"text": "Scott Lang emerges from the Quantum Realm", "query": "Avengers Endgame Scott Lang van"},
                    {"text": "with proof that time moves differently inside it.", "query": "Avengers Endgame Scott Lang explains time dilation"}
                ]
            },
            {
                "text": "Tony Stark solves quantum navigation by inventing the Time-Space GPS, allowing the team to pinpoint exact coordinates.",
                "visual_beats": [
                    {"text": "Tony Stark solves quantum navigation", "query": "Avengers Endgame Tony Stark mobius strip simulation"},
                    {"text": "by inventing the Time-Space GPS,", "query": "Avengers Endgame Tony Stark time space GPS wrist"},
                    {"text": "allowing the team to pinpoint exact coordinates.", "query": "Avengers Endgame Avengers Quantum suits prep"}
                ]
            }
        ]
    })

    monkeypatch.setattr("stages.stage_3.screen_qa._call_llm_chain",
                        lambda *args, **kwargs: (fake_llm_json, "test-model"))

    nar = sq.write_screen_qa(proj, progress=lambda m: None)

    assert isinstance(nar, Narration)
    assert nar.mode == "screen_qa"
    assert nar.title == "How The Avengers Conquered Time Travel"
    assert len(nar.scenes) >= 2
    # Check that movie + year is cited and no comic issue is mentioned
    assert "Avengers: Endgame (2019)" in nar.scenes[0].text
    assert "#" not in nar.scenes[0].text

    # Verify visual_beats contract: dicts with text and query
    for s in nar.scenes:
        assert isinstance(s.visual_beats, list)
        for b in s.visual_beats:
            assert isinstance(b, dict)
            assert "text" in b
            assert "query" in b
            assert _vb_text(b) == b["text"]

    # Test saving narration
    nar_path = sq.save_screen_narration(nar, proj, progress=lambda m: None)
    assert nar_path.exists()
    assert nar_path.name == "narration.json"

    saved = json.loads(nar_path.read_text())
    assert saved["mode"] == "screen_qa"
    assert isinstance(saved["scenes"][0]["visual_beats"][0], dict)
    assert saved["scenes"][0]["visual_beats"][0]["query"] == "Avengers Endgame title card"
