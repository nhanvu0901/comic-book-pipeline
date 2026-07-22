"""Stage 3 GROUNDING CHECK — the text↔shown-panel critic that runs on the FINAL enriched
scenes in save_narration (config GROUNDING_CHECK), SEPARATE from the transparency critic.
It FLAGS + LOGS a narration line that asserts a CONCRETE, drawable place/event/action the
panel it is shown over does not depict (the real Immortal Hulk "died at a gas station"
miss, where every shown panel started at the morgue). It never rewrites or blocks.

These tests mock the LLM call (call_with_chain) so the plumbing is deterministic: a
concrete claim the panel contradicts surfaces as a flag, an abstract meaning line and a
matching panel return zero, missing panel metadata soft-skips WITHOUT an LLM call, and an
offline LLM (RuntimeError) degrades softly instead of crashing."""
import json
import pytest

import stages.stage_3.write_script as ws


def _scenes(rows):
    # rows: list of (text, panel_description) — scene_id is 1-based.
    return [{"scene_id": i + 1, "text": t, "panel_description": pd}
            for i, (t, pd) in enumerate(rows)]


def _mock_flags(monkeypatch, flags):
    def fake(*a, **k):
        return json.dumps({"flags": flags}), "mock-model"
    monkeypatch.setattr(ws, "call_with_chain", fake)


def test_ungrounded_place_claim_is_flagged(monkeypatch):
    # (a) The real Immortal Hulk miss: hook says "gas station", the panel shows a morgue.
    _mock_flags(monkeypatch, [
        {"scene_id": 1, "claim": "Bruce Banner died at a gas station",
         "shown": "a body on a morgue slab behind glass",
         "issue": "the video never shows the gas station it names"},
    ])
    scenes = _scenes([
        ("Bruce Banner died at a gas station.", "A body lies on a morgue slab behind glass."),
        ("The truth was worse than anyone imagined.", "A doctor stares through the window."),
    ])
    flags = ws._grounding_critic(scenes)
    assert len(flags) == 1
    assert "ungrounded_claim" in flags[0]
    assert "gas station" in flags[0] and "morgue" in flags[0]


def test_abstract_meaning_line_is_not_flagged(monkeypatch):
    # (b) An abstract thesis line needs no matching panel — the LLM returns zero flags.
    _mock_flags(monkeypatch, [])
    scenes = _scenes([
        ("No one really wins here.", "Two fighters stand apart in the rubble."),
        ("This was the day everything changed.", "A wide shot of the ruined city."),
    ])
    assert ws._grounding_critic(scenes) == []


def test_claim_matching_panel_is_not_flagged(monkeypatch):
    # (c) The concrete claim IS shown (same action) → grounded, zero flags.
    _mock_flags(monkeypatch, [])
    scenes = _scenes([
        ("He leaps onto the machine.", "A hero mid-leap landing on a huge machine."),
        ("The ship explodes.", "A starship bursting into a fireball."),
    ])
    assert ws._grounding_critic(scenes) == []


def test_missing_panel_metadata_skips_without_llm(monkeypatch):
    # (d) No panel_description on any scene (offline / Q&A pre-stage-5) → soft-skip, and
    # the LLM is NOT called (would crash if it were).
    def boom(*a, **k):
        raise AssertionError("grounding must not call the LLM with no panel metadata")
    monkeypatch.setattr(ws, "call_with_chain", boom)
    scenes = _scenes([("A concrete claim.", ""), ("Another line.", "")])
    assert ws._grounding_critic(scenes) == []


def test_offline_llm_degrades_softly(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("[grounding] all models exhausted")
    monkeypatch.setattr(ws, "call_with_chain", boom)
    scenes = _scenes([("He stabs the king.", "A knife plunges into a throne-room guard.")])
    assert ws._grounding_critic(scenes) == []


def test_disabled_knob_skips_llm(monkeypatch):
    monkeypatch.setattr(ws, "GROUNDING_CHECK", False)

    def boom(*a, **k):
        raise AssertionError("grounding must not call the LLM when disabled")
    monkeypatch.setattr(ws, "call_with_chain", boom)
    scenes = _scenes([("Anything.", "A panel.")])
    assert ws._grounding_critic(scenes) == []
