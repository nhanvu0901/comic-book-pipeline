"""Stage 3 TRANSPARENCY / CLARITY CRITIC — the clarity pass that runs on the FINAL
narration of ALL 3 modes (recap, micro, Q&A) at the pipeline convergence point
(config TRANSPARENCY_CRITIC). It FLAGS + LOGS four clarity failures a first-time viewer
trips on; it never rewrites or blocks by default.

These tests mock the LLM call (call_with_chain) so they assert the plumbing
deterministically: a stranger character / an overstuffed sentence surface as flags, a
clean narration returns zero, and an offline LLM (RuntimeError) degrades softly instead
of crashing."""
import json
import pytest

import stages.stage_3.write_script as ws
from stages.stage_3.schema import Narration, Scene


def _nar(texts, title="Why did the god spare one man?", mode="recap"):
    scenes = [Scene(scene_id=i + 1, text=t, page_ref=0, panel_ref=-1)
              for i, t in enumerate(texts)]
    return Narration(mode=mode, title=title, hook=texts[0] if texts else "", scenes=scenes)


def _mock_flags(monkeypatch, flags):
    def fake(*a, **k):
        return json.dumps({"flags": flags}), "mock-model"
    monkeypatch.setattr(ws, "call_with_chain", fake)


def test_undefined_character_is_flagged(monkeypatch):
    # A stranger name ("Dawn") dropped in with no role clause — the real Silver Surfer miss.
    _mock_flags(monkeypatch, [
        {"scene_id": 2, "type": "undefined_character",
         "issue": "Dawn appears with no introduction — a first-time viewer has no idea who Dawn is"},
    ])
    nar = _nar(["Galactus arrives to consume the planet.",
                "But Dawn finishes his thought and everything changes.",
                "The world is erased anyway."])
    flags = ws._transparency_critic(nar, {"characters": ["Galactus"]}, "micro_moment")
    assert len(flags) == 1
    assert "undefined_character" in flags[0]
    assert ws._transparency_has_heavy(flags)          # heavy → eligible for optional retry


def test_clean_narration_returns_zero(monkeypatch):
    _mock_flags(monkeypatch, [])
    nar = _nar(["Galactus arrives to consume the planet.",
                "The Surfer begs him to spare the people.",
                "Galactus spares one man but erases the world anyway."])
    flags = ws._transparency_critic(nar, {"characters": ["Galactus", "Silver Surfer"]}, "micro_moment")
    assert flags == []
    assert not ws._transparency_has_heavy(flags)


def test_overstuffed_sentence_is_flagged_but_light(monkeypatch):
    # 3+ events chained in one sentence → flagged, but a light nit (not retry-worthy).
    _mock_flags(monkeypatch, [
        {"scene_id": 1, "type": "overstuffed_sentence",
         "issue": "one sentence chains four events with dashes — a listener can't absorb it in one pass"},
    ])
    nar = _nar(["He lands, and draws his blade, and the guards charge, "
                "and the throne room erupts into chaos.",
                "The king falls."])
    flags = ws._transparency_critic(nar, {"characters": ["the king"]}, "recap")
    assert len(flags) == 1
    assert "overstuffed_sentence" in flags[0]
    assert not ws._transparency_has_heavy(flags)       # light → no retry


def test_offline_llm_degrades_softly(monkeypatch):
    # call_with_chain raises (SDK unavailable / offline / no-embed) → skip, never crash.
    def boom(*a, **k):
        raise RuntimeError("[transparency] all models exhausted")
    monkeypatch.setattr(ws, "call_with_chain", boom)
    nar = _nar(["A.", "B.", "C."])
    assert ws._transparency_critic(nar, {"characters": ["X"]}, "explore_answer") == []


def test_disabled_knob_skips_llm(monkeypatch):
    monkeypatch.setattr(ws, "TRANSPARENCY_CRITIC", False)

    def boom(*a, **k):
        raise AssertionError("critic must not call the LLM when disabled")
    monkeypatch.setattr(ws, "call_with_chain", boom)
    nar = _nar(["A.", "B."])
    assert ws._transparency_critic(nar, {}, "recap") == []


def test_too_few_scenes_skips_llm(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("critic must not call the LLM with <2 scenes")
    monkeypatch.setattr(ws, "call_with_chain", boom)
    nar = _nar(["Only one scene."])
    assert ws._transparency_critic(nar, {}, "recap") == []
