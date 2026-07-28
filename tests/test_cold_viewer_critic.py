"""Stage 3 COLD-VIEWER CRITIC — the zero-context WHO/WHY pass that runs on the FINAL
narration of ALL 3 modes at the pipeline convergence point (config COLD_VIEWER_CRITIC),
SIBLING of the transparency critic. It FLAGS + LOGS scenes a first-time viewer can't
follow for lack of a relationship/reason; it never blocks by default.

These tests mock the LLM call (call_with_chain) so the plumbing is deterministic: a
missing-relationship gap surfaces as a flag carrying its suggested clause, a clean
narration returns zero, the knob OFF / <2 scenes / offline LLM all skip softly, and the
fix-block formatter turns flags into a writer-facing WEAVE-THE-WHY block."""
import json

import stages.stage_3.write_script as ws
from stages.stage_3.schema import Narration, Scene


def _nar(texts, title="Harley Quinn", mode="micro_moment"):
    scenes = [Scene(scene_id=i + 1, text=t, page_ref=0, panel_ref=-1)
              for i, t in enumerate(texts)]
    return Narration(mode=mode, title=title, hook=texts[0] if texts else "", scenes=scenes)


def _mock_flags(monkeypatch, flags):
    def fake(*a, **k):
        return json.dumps({"flags": flags}), "mock-model"
    monkeypatch.setattr(ws, "call_with_chain", fake)


def test_missing_relationship_is_flagged_with_clause(monkeypatch):
    # The real harley-quinn miss: narration beats the Joker bloody but never says he was
    # her abusive ex — a cold viewer has no idea why it lands.
    _mock_flags(monkeypatch, [
        {"scene_id": 2, "missing": "relationship",
         "note": "the narration never says what Harley and the Joker are to each other",
         "suggested_clause": "the man who spent years breaking her"},
    ])
    nar = _nar(["Harley walks into the Joker's cell.",
                "The Joker grabs her throat and promises to kill her.",
                "She beats him bloody and walks out."])
    flags = ws._cold_viewer_critic(nar, {"characters": ["Harley Quinn", "Joker"]}, "micro_moment")
    assert len(flags) == 1
    assert flags[0].startswith("cold_viewer[relationship]: scene S2:")
    assert "suggest: the man who spent years breaking her" in flags[0]


def test_clean_narration_returns_zero(monkeypatch):
    _mock_flags(monkeypatch, [])
    nar = _nar(["Harley, once the Joker's abused partner, walks into his cell.",
                "The man who broke her for years grabs her throat.",
                "She beats him bloody and finally walks away for good."])
    flags = ws._cold_viewer_critic(nar, {"characters": ["Harley Quinn", "Joker"]}, "micro_moment")
    assert flags == []


def test_disabled_knob_skips_llm(monkeypatch):
    monkeypatch.setattr(ws, "COLD_VIEWER_CRITIC", False)

    def boom(*a, **k):
        raise AssertionError("critic must not call the LLM when disabled")
    monkeypatch.setattr(ws, "call_with_chain", boom)
    nar = _nar(["A.", "B."])
    assert ws._cold_viewer_critic(nar, {}, "recap") == []


def test_too_few_scenes_skips_llm(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("critic must not call the LLM with <2 scenes")
    monkeypatch.setattr(ws, "call_with_chain", boom)
    nar = _nar(["Only one scene."])
    assert ws._cold_viewer_critic(nar, {}, "recap") == []


def test_offline_llm_degrades_softly(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("[cold_viewer] all models exhausted")
    monkeypatch.setattr(ws, "call_with_chain", boom)
    nar = _nar(["A.", "B.", "C."])
    assert ws._cold_viewer_critic(nar, {"characters": ["X"]}, "explore_answer") == []


def test_flag_without_note_dropped(monkeypatch):
    # A flag with no note carries nothing actionable — it must not become a string flag.
    _mock_flags(monkeypatch, [{"scene_id": 3, "missing": "why", "note": "", "suggested_clause": "x"}])
    nar = _nar(["A.", "B.", "C."])
    assert ws._cold_viewer_critic(nar, {}, "recap") == []


def test_format_fixes_builds_weave_block():
    flags = [
        "cold_viewer[relationship]: scene S2: viewer never told who they are to each other "
        "| suggest: the man who spent years breaking her",
        "cold_viewer[why]: scene S6: her goal is never stated",   # no suggested clause
    ]
    block = ws._format_cold_viewer_fixes(flags)
    assert block.startswith("PREVIOUS DRAFT LEFT A ZERO-CONTEXT VIEWER CONFUSED")
    assert "scene 2:" in block and "weave in a plain clause like: the man who spent years breaking her" in block
    assert "scene 6:" in block and "state the missing why in plain words" in block


def test_format_fixes_empty_on_no_flags():
    assert ws._format_cold_viewer_fixes([]) == ""
    assert ws._format_cold_viewer_fixes(["not a cold_viewer flag string"]) == ""
