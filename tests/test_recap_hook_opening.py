"""Recap's spoken teaser must stay sourced and lead into a new first beat."""

import json

import stages.stage_3.write_script as ws
from stages.stage_3.schema import Beat, Glossary


def test_intro_failure_uses_a_fact_safe_question(monkeypatch):
    def unavailable(**_kwargs):
        raise RuntimeError("offline")

    monkeypatch.setattr(ws, "call_with_chain", unavailable)
    intro = ws.generate_intro({
        "title": "Batman and the Joker",
        "characters": ["Batman"],
        "plot_summary": "Batman follows the Joker into a warehouse.",
    })
    assert intro["intro_line"] == "What happened to Batman?"


def test_writer_receives_spoken_teaser_so_scene_one_can_advance(monkeypatch):
    captured = {}

    def capture(*, system, user, **_kwargs):
        captured["user"] = user
        return json.dumps({"scenes": [{"text": "Batman follows the Joker into a warehouse.",
                                       "connective": None, "beat_id": 1}]}), "fake"

    monkeypatch.setattr(ws, "call_with_chain", capture)
    beat = Beat(id=1, function="COLD_OPEN", name="Warehouse", page_refs=[1],
                summary="Batman follows the Joker into a warehouse.")
    ws.write_scenes([beat], Glossary(), {
        "title": "Batman and the Joker",
        "plot_summary": "Batman follows the Joker into a warehouse.",
    }, [], "recap_summary", opening_teaser="Batman finds the Joker in a warehouse.")
    assert "OPENING TEASER ALREADY SPOKEN: Batman finds the Joker in a warehouse." in captured["user"]
    assert "add a new sourced detail" in captured["user"]
