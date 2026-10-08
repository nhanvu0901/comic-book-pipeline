"""Tests for stages/stage_1/screen_research.py (Task 3)."""
import json
from pathlib import Path
import pytest

from stages.stage_1 import screen_research as sr
from config import get_project_dirs


def test_clean_screen_items():
    raw_items = [
        {
            "entity": "Tony Stark",
            "event": "Builds time-space GPS",
            "adaptation_title": "Avengers: Endgame",
            "year": 2019,
            "summary": "Tony figures out the inverted Mobius strip model to navigate the Quantum Realm.",
            "visual_query": "Avengers Endgame Tony Stark discovers time travel GPS",
            "source_urls": ["https://marvelcinematicuniverse.fandom.com/wiki/Time-Space_GPS"],
        },
        {
            # Missing visual_query and source_urls
            "entity": "Scott Lang",
            "event": "Proposes Quantum Realm heist",
            "adaptation_title": "Avengers: Endgame",
            "year": "2019",
            "summary": "Scott tells Steve and Natasha about the Quantum Realm time dilation.",
        }
    ]
    cleaned = sr._clean_screen_items(raw_items)
    assert len(cleaned) == 2
    assert cleaned[0]["entity"] == "Tony Stark"
    assert cleaned[0]["year"] == 2019
    assert cleaned[1]["visual_query"] == "Avengers: Endgame Scott Lang Proposes Quantum Realm heist"
    assert isinstance(cleaned[1]["source_urls"], list)


def test_save_screen_context(tmp_path, monkeypatch):
    proj = "test_screen_project"
    monkeypatch.setattr("stages.stage_1.screen_research.get_project_dirs",
                        lambda p: {"root": tmp_path, "preprocessed": tmp_path / "preprocessed"})

    data = {
        "question": "How did the Avengers travel back in time in Endgame?",
        "items": [
            {
                "entity": "Tony Stark",
                "event": "Navigates Quantum Realm",
                "adaptation_title": "Avengers: Endgame",
                "year": 2019,
                "summary": "Solves quantum navigation.",
                "visual_query": "Avengers Endgame Tony Stark GPS",
                "source_urls": ["https://en.wikipedia.org/wiki/Avengers:_Endgame"],
            }
        ]
    }

    out_path = sr.save_screen_context(proj, data)
    assert out_path.exists()
    assert out_path.name == "screen_context.json"

    saved = json.loads(out_path.read_text())
    assert saved["question"] == data["question"]
    assert len(saved["items"]) == 1
    item = saved["items"][0]
    for key in ("entity", "event", "adaptation_title", "year", "summary", "visual_query", "source_urls"):
        assert key in item


def test_research_screen_canon_mock(monkeypatch):
    # Mock YouComClient
    class FakeRawCall:
        ok = True
        error = None
        payload = {"hits": [{"url": "https://marvelcinematicuniverse.fandom.com/wiki/Time_Heist", "snippets": ["Time Heist in Endgame"]}]}

    monkeypatch.setattr("stages.stage_1.screen_research.YouComClient.search",
                        lambda self, q, profile=None: FakeRawCall())

    fake_response = json.dumps({
        "question": "How did the Avengers travel back in time in Endgame?",
        "items": [
            {
                "entity": "Scott Lang",
                "event": "Introduces Quantum Realm concept",
                "adaptation_title": "Avengers: Endgame",
                "year": 2019,
                "summary": "Scott Lang returns from the Quantum Realm and proposes using it to travel back in time.",
                "visual_query": "Avengers Endgame Scott Lang quantum realm",
                "source_urls": ["https://marvelcinematicuniverse.fandom.com/wiki/Time_Heist"],
            },
            {
                "entity": "Tony Stark",
                "event": "Invents the Time-Space GPS",
                "adaptation_title": "Avengers: Endgame",
                "year": 2019,
                "summary": "Tony models the inverted Mobius strip to safely navigate through the Quantum Realm.",
                "visual_query": "Avengers Endgame Tony Stark time space GPS",
                "source_urls": ["https://marvelcinematicuniverse.fandom.com/wiki/Time-Space_GPS"],
            },
            {
                "entity": "Hank Pym",
                "event": "Supplies Pym Particles for the journey",
                "adaptation_title": "Avengers: Endgame",
                "year": 2019,
                "summary": "The team uses Hank Pym's shrinking particles to enter the Quantum Realm.",
                "visual_query": "Avengers Endgame Pym particles time heist",
                "source_urls": ["https://marvelcinematicuniverse.fandom.com/wiki/Pym_Particles"],
            }
        ]
    })

    monkeypatch.setattr("stages.stage_1.screen_research._call_openrouter",
                        lambda *args, **kwargs: fake_response)

    res = sr.research_screen_canon("How did the Avengers travel back in time in Endgame?")
    assert res["question"] == "How did the Avengers travel back in time in Endgame?"
    assert len(res["items"]) == 3
    assert res["items"][0]["entity"] == "Scott Lang"
    assert res["items"][1]["adaptation_title"] == "Avengers: Endgame"


def test_screen_research_does_not_call_batcave_or_answer_research(monkeypatch):
    import stages.stage_1.answer_research as ar

    def _boom(*a, **k):
        raise AssertionError("answer_research.build_contexts must NOT be called by screen_research")

    monkeypatch.setattr(ar, "build_contexts", _boom)

    # Calling save_screen_context should succeed without touching build_contexts
    context = {
        "question": "Who died in Infinity War?",
        "items": [{
            "entity": "Loki",
            "event": "Killed by Thanos",
            "adaptation_title": "Avengers: Infinity War",
            "year": 2018,
            "summary": "Thanos chokes Loki in the opening.",
            "visual_query": "Infinity War Thanos kills Loki",
            "source_urls": ["https://en.wikipedia.org/wiki/Avengers:_Infinity_War"]
        }]
    }
    # save_screen_context directly
    path = sr.save_screen_context("test_proj", context)
    assert path.name == "screen_context.json"
